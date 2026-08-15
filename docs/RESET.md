# 전체 초기화 절차

이 문서는 Astral Party Korean Patch의 Neon DB, GitHub Releases/tags, `distribution` branch를 모두 비우고 현재 코드 기준으로 처음부터 다시 채우는 절차입니다.

현재 승인 번역은 새 DB를 백업/복원하지 않습니다. **이전 번역 관리 프로젝트 `astral-control-site` DB의 `lang_data.ko`와 `astral_data.ko`를 다시 가져옵니다.** 이전 사이트에서 승인 대기 중인 값은 로그 테이블에만 있으므로 production `ko` 값만 이관하면 승인된 번역만 복원됩니다.

## 1. 로컬 DB 도구 준비

DB reset/migration/import에는 Builder 전체나 Python 3.12가 필요하지 않습니다. **Python 3.9 이상**의 현재 `python`을 사용합니다.

```powershell
python --version
python -m pip install -r .\database\requirements.txt
```

`database/requirements.txt`에는 DB 작업에 필요한 `psycopg`만 들어 있습니다.

DB 도구는 프로젝트 루트의 `.env`를 자동으로 읽습니다. 따라서 다음 값을 PowerShell `$env:`에 다시 설정할 필요가 없습니다.

```text
NEON_DATABASE_URL
NEON_DATABASE_URL_DIRECT
```

이전 번역 DB도 현재 작업공간에서는 자동으로 다음 파일을 찾습니다.

```text
..\Project\Nuxt\astral-control-site\.env
```

그 파일의 `DATABASE_URL`을 legacy DB로 사용합니다. 다른 PC/경로에서 작업할 때만 현재 프로젝트 `.env`에 `LEGACY_DATABASE_URL`을 추가하거나 importer에 `--legacy-env`를 지정하면 됩니다.

## 2. 새 코드 push

```powershell
git push origin main
```

새 baseline migration의 `0001_initial.sql` checksum은 구 DB와 다르므로 reset 전 `Patch` workflow가 실행되면 migration 단계에서 실패할 수 있습니다. 전환 중 해당 run은 취소하면 됩니다.

## 3. Neon DB 초기화

프로젝트가 소유하는 table/view/function만 제거합니다. Neon database 전체 `public` schema를 drop하지 않습니다.

```powershell
python .\database\reset.py --confirm RESET_ASTRAL_DATABASE
python .\database\migrate.py
```

두 명령 모두 루트 `.env`의 `NEON_DATABASE_URL_DIRECT`를 자동 사용합니다.

정상 결과:

```text
Astral Party database objects removed.
applied migrations: 1
```

이 시점에는 새 schema만 있고 원문/번역 데이터는 없습니다.

## 4. GitHub Releases와 tags 전체 삭제

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

로컬 tag도 비우려면:

```powershell
@(git tag -l) | ForEach-Object {
    if ($_) { git tag -d $_ }
}
```

## 5. distribution branch 초기화

```powershell
git ls-remote --exit-code --heads origin refs/heads/distribution *> $null
if ($LASTEXITCODE -eq 0) {
    git push origin --delete distribution
}
```

다음 Patch/AutoPatcher/APK workflow가 필요할 때 `distribution` branch를 자동 생성합니다.

## 6. 현재 INT_STEAM 원문과 route metadata 채우기

Actions에서 `Patch`를 수동 실행합니다.

```text
mode = pre
```

빈 DB의 첫 실행은 현재 INT_STEAM 원문을 모두 최초 source version으로 저장하고 현재 revision을 하나의 source-change group으로 기록합니다.

```text
INT_STEAM source scan
  -> translation_units
  -> source_versions
  -> source_changes(current revision)
  -> current source pointer

CN_STEAM / INT_ANDROID
  -> route compatibility metadata only
```

아직 번역 이관 전이므로 pre patch는 대부분 원문 fallback을 사용합니다. `develop` channel이므로 정식 `release` channel을 대체하지 않습니다.

## 7. 이전 프로젝트 DB에서 승인 번역 재이관

첫 `Patch mode=pre`가 성공해 `translation_units`가 생긴 뒤 다음 한 줄을 실행합니다.

```powershell
python .\tools\database\import_legacy_translations.py
```

현재 작업공간에서는 별도 URL 입력이 필요 없습니다.

```text
현재 새 DB
  <- .\.env 의 NEON_DATABASE_URL

이전 번역 DB
  <- ..\Project\Nuxt\astral-control-site\.env 의 DATABASE_URL
```

이전 DB에서 가져오는 값:

```text
lang_data
  WHERE is_deleted=false AND ko <> ''

astral_data
  WHERE is_deleted=false AND ko <> ''
```

이 값들은 새 DB에 직접 우회 INSERT하지 않고 다음 승인 audit 경로로 들어갑니다.

```text
Legacy control-site translation import (group)
  -> translation_changes(status=approved)
  -> translations production row
```

출력 예:

```text
Legacy DB configuration: C:\...\Project\Nuxt\astral-control-site\.env
legacy=8511 inserted=8511 updated=0 unchanged=0 missing_current_unit=0
```

`missing_current_unit`은 이전 DB에는 있지만 현재 INT_STEAM에서 사라진 identity 수입니다. 이 항목은 현재 production 번역에 넣지 않습니다.

같은 importer를 다시 실행해도 동일한 production 값은 `unchanged`로 처리하고 빈 변경 그룹을 새로 만들지 않습니다.

이전 프로젝트 위치가 다른 경우:

```powershell
python .\tools\database\import_legacy_translations.py `
  --legacy-env 'D:\path\to\astral-control-site\.env'
```

또는 현재 `.env`에 다음 키를 둘 수 있습니다.

```text
LEGACY_DATABASE_URL=...
```

## 8. 정식 한글패치 생성

승인 번역 이관 후 Actions에서:

```text
Patch
→ mode = release
```

최종 Release 이름은 세 route의 현재 revision 중 가장 큰 값을 사용합니다. 세 route가 모두 r116이면:

```text
v3.2.0_r116
├─ INT_STEAM_manifest.json
├─ CN_STEAM_manifest.json
├─ INT_ANDROID_manifest.json
└─ route별 payload
```

예를 들어 Android만 r117이면 `v3.2.0_r117`이 됩니다. 같은 최고 revision을 다시 정식 배포하면 기존 Release를 교체하지 않고 `_p2`, `_p3`가 붙습니다.

## 9. APK와 AutoPatcher 다시 생성

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

```powershell
$repo = gh repo view --json nameWithOwner --jq '.nameWithOwner'
$artifactIds = @(gh api "repos/$repo/actions/artifacts?per_page=100" --paginate --jq '.artifacts[].id')
foreach ($id in $artifactIds) {
    if ($id) { gh api --method DELETE "repos/$repo/actions/artifacts/$id" }
}
```
