# Astral Party Korean Patch - Implementation Plan

## 구현 상태 (2026-08-15)

Phase 0~8의 로컬 구현과 테스트용 통합 검증은 완료했습니다. Builder/DB/Patcher/Release protocol/GitHub Actions workflow가 모두 monorepo에 구현되어 있습니다.

현재 운영 구조는 INT_STEAM 하나를 canonical translation source로 사용하고, CN_STEAM/INT_ANDROID는 route target compatibility만 별도로 관리하도록 단순화했습니다. hosted Actions에서 `Patch -> unified build/release`, AutoPatcher, Android APK의 실제 운영 integration을 계속 검증합니다. 설정 절차는 [`OPERATIONS.md`](OPERATIONS.md)를 참조합니다.

## 1. 기본 원칙

1. Builder와 Patcher는 하나의 monorepo에 저장한다.
2. 게임 에셋 다운로드, 데이터 추출, Neon DB 동기화, 패치 생성, 검증, 배포는 GitHub Actions에서만 수행한다.
3. 로컬에 보관된 게임 파일은 개발 중 포맷 분석과 fixture 제작 참고용일 뿐 운영 빌드의 입력이 아니다.
4. 기존 프로젝트의 코드는 이식하지 않는다. 게임 포맷 및 패치 대상에 대해 검증된 지식만 재사용한다.
5. 게임 원본 Bundle이나 빌드 산출물을 Git에 영구 저장하지 않는다.
6. Builder와 Patcher의 계약은 versioned manifest/schema로 제한한다.
7. 모든 주요 구현 단위는 테스트 통과 후 Git commit으로 기록한다.

## 2. 재사용하는 도메인 지식

- Unity 2022.3 계열 IL2CPP 게임이다.
- Addressables catalog와 AssetBundle을 사용한다.
- INT Steam에서 Lang TextAsset, STR TextAsset, TMP font/atlas, `data.unity3d`의 legacy font를 패치해야 한다.
- Addressables 캐시는 `root_id/<bundle cache hash>/__data` 형태를 사용한다.
- STR 데이터에는 여러 언어 필드가 있으며 INT Steam은 기존 언어 필드 하나를 한국어로 대체한다.
- 기존 코드/DB/workflow/rule 포맷은 신규 설계의 API로 간주하지 않는다.

## 3. 목표 아키텍처

```text
AstralPartyKorean/
├─ builder/
├─ patcher/
├─ database/
├─ schemas/
├─ routes/
├─ resources/
├─ tests/
├─ docs/
└─ .github/workflows/
```

### Builder 책임

게임 업데이트 감지 → Catalog 해석 → 필요한 Bundle만 다운로드 → 원문 추출 → Neon 동기화 → immutable translation snapshot 생성 → 패치 생성 → 재검증 → manifest/release 생성.

### Patcher 책임

게임 설치 경로/route/revision 탐지 → 호환 release 선택 → 다운로드 및 SHA-256 검증 → transactional install → ownership 기록 → rollback/remove.

Patcher는 Neon DB에 직접 접근하지 않는다.

## 4. 구현 단계

### Phase 0 - Repository foundation

- Git 저장소와 monorepo 디렉터리 구성
- Python builder 패키지 골격
- DB migrations 디렉터리
- shared JSON schema 및 route configuration 위치 확정
- CI 기본 lint/test workflow

완료 기준: 빈 프로젝트가 일관된 명령으로 lint/test 가능.

### Phase 1 - Game source and Addressables resolver

- 게임 version/hotaddress API client
- Catalog downloader
- Unity Addressables catalog parser
- `m_KeyDataString`, `m_BucketDataString`, `m_EntryDataString`, dependencies 해석
- logical asset/key → exact internal bundle mapping
- remote bundle downloader
- target bundle content-addressed cache

완료 기준: INT_STEAM 최신 revision에서 Lang/STR/TMP font 대상에 필요한 bundle만 정확히 식별하고 다운로드 가능.

### Phase 2 - Pure extractors/formats

- Lang XML decoder/encoder
- STR binary decoder/encoder
- Unity object discovery layer
- canonical translation-unit model
- round-trip fixtures/tests

완료 기준: DB 없이 bytes ↔ canonical records 변환이 테스트됨.

### Phase 3 - Neon PostgreSQL from scratch

초기 핵심 모델:

- `game_revisions`
- `asset_locations`
- `translation_units`
- `source_versions`
- `source_changes`
- `translation_change_groups`
- `translation_changes`
- `translations`
- `builds`
- `build_files`

원칙:

- INT_STEAM revision을 원문 변경 그룹으로 사용
- source fingerprint가 새로 생긴 경우에만 source version 저장
- added/modified/removed 원문 변경을 `source_changes`로 기록하고 즉시 적용
- `translations`에는 승인된 production 번역만 저장
- pending/rejected/superseded 제안은 `translation_changes`에 보존
- migration이 DB schema의 유일한 정의
- migration/builder/editor 역할 분리 가능하도록 권한 경계 설계
- Actions runtime에는 Neon pooled connection, migration에는 필요 시 direct connection 사용

완료 기준: 신규 DB에서 migrate → extract sync → revision diff → translation 보존 테스트 통과.

### Phase 4 - Patch builder

입력:

- resolved revision assets
- immutable translation snapshot
- route config
- Korean font resources

출력:

- patched Addressables cache files
- patched `data.unity3d`
- build metadata

완료 기준: Lang/STR/TMP/legacy font 패치를 pure build API로 생성 가능.

### Phase 5 - Validator

- patched Unity bundle 재로딩
- Lang XML parse/key checks
- STR decode/encode/decode round-trip 및 ID-set checks
- TMP/font target replacement checks
- AssetBundle `m_Name` 보존 확인
- output SHA-256 및 file-set 검증

완료 기준: validation failure 시 release 단계로 진행하지 않음.

### Phase 6 - Shared release protocol

- `patch-manifest.schema.json`
- route/version/revision/catalog hash compatibility
- build file target/path/final size/SHA-256
- gzip transport size/SHA-256와 해제 후 payload size/SHA-256 이중 검증
- release index format
- immutable manifest 규칙

완료 기준: builder가 schema-valid manifest를 생성하고 fixture tests 통과.

### Phase 7 - Patcher V3

단일 실행 파일 Rust CLI `AstralAutoPatcher` 구현:

- Steam app `2622000` 탐지 및 Steam 게임 경로 수동 설정
- LocalLow Addressables 경로 탐지 및 LocalLow 경로 수동 설정
- `%LOCALAPPDATA%\AstralAutoPatcher` self-registration
- `astral://install|remove|settings`와 `astral://<action>/<INT_STEAM|CN_STEAM>` URI protocol 처리
- Ratatui/Crossterm 기반 고정 화면 TUI, 키보드/마우스 메뉴 조작
- URI 진입에서도 Patcher/경로/게임/Catalog/채널/설치 상태를 먼저 표시
- installed game revision/catalog hash 탐지
- compatible release resolution
- staged download
- checksum validation
- backup
- transactional install
- install ownership manifest
- safe rollback/remove

절대 전체 `AssetBundles` 캐시를 삭제하지 않는다.

완료 기준: fixture/test installation root에서 install → verify → rollback → remove 통합 테스트 통과.

### Phase 8 - GitHub Actions automation

Actions 화면에는 사용자 관점의 네 workflow만 노출합니다.

1. `CI`: Builder/Patcher lint와 test.
2. `Patch`: 자동 pre와 수동 release를 하나의 pipeline으로 처리.
3. `AutoPatcher`: 수동 Windows EXE release.
4. `INT_ANDROID APK`: 수동 Android APK release.

`Patch` 내부 동작은 다음과 같습니다.

```text
scheduled pre
  -> 세 route remote revision/catalog lightweight check 병렬 실행
  -> 하나라도 변경되면 실제 sync가 필요한 route만 병렬 sync
  -> INT_STEAM만 canonical source change 적용
  -> Steam legacy inputs는 sync와 병렬 취득
  -> 세 route build + validation 병렬 실행
  -> 하나의 v<game>_r<highest-route-revision>-pre rolling prerelease

manual release
  -> 세 route lightweight check 후 필요한 route만 sync
  -> 같은 INT_STEAM canonical production translation snapshot 사용
  -> 세 route build + validation 병렬 실행
  -> v<game>_r<highest-route-revision> immutable release
  -> 같은 최고 revision 재배포 시 _p2, _p3 ... patch revision 증가
```

번역 승인 자체는 release를 트리거하지 않습니다. `translations` production row가 있으면 그 값을 사용하고 없으면 현재 원문을 유지합니다. pending/rejected/superseded proposal은 빌드 입력에 포함되지 않습니다.

세 route별 manifest는 한 Release에 함께 배포합니다. payload filename에는 route prefix를 붙여 asset 충돌을 방지합니다.

rolling machine metadata는 Releases 화면에 별도 배포물로 만들지 않고 `distribution` branch의 `release-index.json`, `patcher-index.json`, `android-apk-index.json`으로 관리합니다. 기존 client 호환을 위해 이미 존재하는 legacy index Release에는 같은 파일을 mirror하되 새 legacy index Release를 생성하지 않습니다.

Release 표시 이름은 다음 규칙을 사용합니다.

```text
AutoPatcher v<patcher-version>
INT_ANDROID v<game-version>
v<game-version>_r<highest-route-revision>
```


## 5. DB 동기화 규칙

번역 source와 route compatibility를 분리합니다.

- `INT_STEAM`만 canonical translation source다.
- `CN_STEAM`/`INT_ANDROID`는 `game_revisions`와 `asset_locations`로 target catalog/bundle 호환 정보만 저장한다.
- 동일 key + 동일 source fingerprint: source row를 새로 만들지 않는다.
- 신규/변경 key: `source_versions`를 생성 또는 재사용하고 현재 pointer를 교체한다.
- 삭제 key: current source pointer를 비우고 revision의 `source_changes`에 removed를 기록한다.
- `game_revisions`의 INT_STEAM revision 하나가 원문 change group이다.
- `translations`는 승인된 production 값만 가진다. source change가 발생해도 기존 승인값은 유지한다.
- 번역 수정은 `translation_changes=pending`에 먼저 들어가며 승인 transaction만 `translations`를 변경한다.
- patch build는 production translation이 있으면 사용하고 없으면 현재 원문을 사용한다.

빌드 중에는 현재 source + production translation snapshot fingerprint를 build metadata에 기록합니다.


## 6. Patch installation protocol

설치 순서:

1. manifest 확보
2. game compatibility 검사
3. 필요한 gzip transport asset만 다운로드
4. transport size/SHA-256 검증
5. staging에서 gzip 해제하며 최종 payload size/SHA-256 재검증
6. 변경 파일 원본 backup
7. install plan 적용
8. 설치 후 SHA-256 재검증
9. `installed.json` atomic write
10. 실패 시 rollback

제거 시 remote rule/release를 조회하지 않고 install-time ownership manifest만 사용한다.
현재 파일 hash가 설치 당시 값과 다르면 임의 삭제/복원을 하지 않는다.

## 7. Commit policy

주요 완료 단위마다 commit:

- repository foundation
- addressables resolver
- extractors/formats
- initial Neon schema and sync
- patch builder
- validator
- release protocol
- patcher install engine
- GitHub Actions pipeline

대규모 작업 중에도 테스트 가능한 독립 단계가 완성되면 추가 commit을 허용한다.


## INT_ANDROID Android APK + AutoPatcher

- Google Play에서 `apkeep`으로 APK/split APK를 취득하고 APKEditor로 병합하되 원본 Play signature metadata를 유지.
- legacy font/runtime 변경과 zipalign을 먼저 수행한 뒤 원본 Play APK Signing Block을 중간 수정본에 이식.
- JingMatrix/LSPatch v0.8 sigbypass level 2를 마지막에 적용해 이식된 signing block의 원본 Play 인증서를 runtime config에 보존하고 최종 nested APK를 생성.
- `assets/bin/Data/data.unity3d` legacy TTF 교체 후 `com.astralpatch.runtime.BootstrapActivity`와 runtime DEX를 주입.
- Android APK는 Actions Secrets의 장기 signing key로 계속 서명하고 `INT_ANDROID v<game-version>` GitHub Release + `distribution/android-apk-index.json`으로 배포.
- Windows AstralAutoPatcher가 Google Platform-Tools를 자체 관리하고 실제 USB 기기 및 MuMu/BlueStacks/LDPlayer를 자동 탐색.
- AutoPatcher는 APK size/SHA-256을 검증하고 `installerPackageName=com.android.vending`으로 설치한 뒤 installer를 재검증.
- 공식판에서 최초 전환할 때 다른 signature 때문에 제거가 필요하면 앱 데이터 삭제 가능성을 경고하고 사용자 확인을 받은 뒤 진행.
- Addressables patch release는 LANG/STR/TMP bundle만 포함하고 각 entry에 source SHA-256/size를 기록.
- APK가 바뀌지 않는 catalog-only update는 APK 재배포 없이 새 INT_ANDROID patch release만 생성.
