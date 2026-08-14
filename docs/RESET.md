# 전체 초기화 절차

이 문서는 Astral Party Korean Patch의 Neon DB, GitHub Releases/tags, `distribution` branch를 모두 비우고 현재 코드 기준으로 처음부터 다시 채우는 절차입니다.

> 이 작업은 파괴적입니다. 특히 DB reset 전에 승인 번역 backup을 반드시 생성합니다.

## 1. 로컬 도구 준비

PowerShell 기준:

```powershell
py -3.12 -m pip install -e '.\builder[dev]'
```

Neon 연결 문자열은 현재 운영 DB 값을 사용합니다.

```powershell
$env:DATABASE_URL = '<NEON_POOLED_URL>'
$env:DATABASE_URL_DIRECT = '<NEON_DIRECT_URL>'
```

## 2. 기존 승인 번역 backup

현재 DB가 구 schema여도 backup tool이 자동 감지합니다.

```powershell
python .\tools\database\translation_backup.py export `
  --file .\translations-backup.json
```

출력 예:

```text
exported approved translations: 8511 -> translations-backup.json
```

`one-shot-neon-approve`로 만든 과거 임시 원문 fallback row는 export에서 제외합니다. `translations-backup*.json`은 `.gitignore`에 포함되어 있으며 Git에 올리지 않습니다.

파일을 열어 `translations` 배열과 개수를 확인한 뒤 다음 단계로 진행합니다.

## 3. 새 코드 push

```powershell
git push origin main
```

새 baseline migration의 `0001_initial.sql` checksum은 구 DB와 다르므로 reset 전 자동 `Patch` workflow가 우연히 실행되면 migration 단계에서 실패할 수 있습니다. 이 전환 구간의 실패 run은 취소해도 되며 DB를 손상시키지 않습니다.

## 4. Neon DB 초기화

프로젝트가 소유하는 table/view/function만 제거합니다. Neon database 전체 `public` schema를 drop하지 않습니다.

```powershell
python .\database\reset.py --confirm RESET_ASTRAL_DATABASE
python .\database\migrate.py
```

정상 결과:

```text
Astral Party database objects removed.
applied migrations: 1
```

이 시점에는 schema만 있고 원문/번역 데이터는 없습니다.

## 5. GitHub Releases와 tags 전체 삭제

GitHub CLI 로그인 상태를 먼저 확인합니다.

```powershell
gh auth status
```

현재 repository의 모든 GitHub Release를 삭제하면서 연결된 tag도 정리합니다.

```powershell
$releaseTags = @(gh release list --limit 1000 --json tagName --jq '.[].tagName')
foreach ($tag in $releaseTags) {
    if ($tag) {
        gh release delete $tag --yes --cleanup-tag
    }
}
```

Release에 연결되지 않은 remote tag도 모두 삭제합니다.

```powershell
$remoteTags = @(
    git ls-remote --tags --refs origin |
        ForEach-Object { (($_ -split '\s+')[1] -replace '^refs/tags/', '') }
)
foreach ($tag in $remoteTags) {
    if ($tag) {
        git push origin ":refs/tags/$tag"
    }
}
```

로컬 tag도 비우고 싶다면:

```powershell
@(git tag -l) | ForEach-Object {
    if ($_) { git tag -d $_ }
}
```

## 6. distribution branch 초기화

`release-index.json`, `patcher-index.json`, `android-apk-index.json`도 처음부터 만들기 위해 remote `distribution` branch를 삭제합니다.

```powershell
git ls-remote --exit-code --heads origin refs/heads/distribution *> $null
if ($LASTEXITCODE -eq 0) {
    git push origin --delete distribution
}
```

다음 Patch/AutoPatcher/APK workflow가 필요할 때 branch를 자동으로 다시 생성합니다.

## 7. 원문과 route metadata 다시 채우기

Actions에서 `Patch`를 수동 실행하고:

```text
mode = pre
```

를 선택합니다.

이 첫 pre run은 다음을 수행합니다.

```text
INT_STEAM source scan
  -> translation_units 생성
  -> source_versions 생성
  -> source_changes를 현재 revision 그룹으로 기록
  -> 현재 source pointer 적용

CN_STEAM / INT_ANDROID
  -> route compatibility metadata만 생성

세 route pre build/validation
  -> v<game>_r<revision>-pre prerelease
  -> distribution/release-index.json 생성
```

아직 승인 번역을 복원하지 않았으므로 이 pre build는 원문 fallback이 많지만 `develop` channel이라 일반 사용자의 `release` channel을 대체하지 않습니다.

## 8. 승인 번역 복원

첫 `Patch mode=pre`가 성공한 뒤 로컬에서:

```powershell
$env:DATABASE_URL = '<NEON_POOLED_URL>'
python .\tools\database\translation_backup.py import `
  --file .\translations-backup.json
```

복원 시 각 값은 단순 `translations` INSERT가 아니라 다음 audit 경로로 들어갑니다.

```text
translation_change_groups
  -> translation_changes(status=approved)
  -> translations production row
```

즉 reset 후에도 새 DB의 승인 invariant를 지킵니다.

출력의 `missing_current_unit`이 0인지 확인합니다. 0이 아니면 현재 게임에서 사라진 과거 번역이므로 production table에는 복원하지 않습니다.

## 9. 정식 한글패치 Release 생성

Actions에서 다시 `Patch`를 실행하고:

```text
mode = release
```

를 선택합니다.

최종 Release 예:

```text
v3.2.0_r116
├─ INT_STEAM_manifest.json
├─ CN_STEAM_manifest.json
├─ INT_ANDROID_manifest.json
└─ route별 payload
```

## 10. APK와 AutoPatcher 다시 생성

Release를 모두 삭제했으므로 사용자 배포물도 다시 만듭니다.

1. `INT_ANDROID APK` 수동 실행
2. `AutoPatcher` 수동 실행

최종 사용자 Release 이름:

```text
AutoPatcher v0.8.x
INT_ANDROID v3.2.0
v3.2.0_r116
```

자동 pre prerelease는 별도로 `v3.2.0_r116-pre`가 존재할 수 있습니다.

## 선택: Actions artifact까지 삭제

Release/tag와 무관한 과거 Actions artifact까지 지우고 싶을 때만 실행합니다.

```powershell
$repo = gh repo view --json nameWithOwner --jq '.nameWithOwner'
$artifactIds = @(gh api "repos/$repo/actions/artifacts?per_page=100" --paginate --jq '.artifacts[].id')
foreach ($id in $artifactIds) {
    if ($id) { gh api --method DELETE "repos/$repo/actions/artifacts/$id" }
}
```
