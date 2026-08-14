# Astral Party Korean Patch - Implementation Plan

## 구현 상태 (2026-08-13)

Phase 0~8의 로컬 구현과 테스트용 통합 검증은 완료했습니다. Builder/DB/Patcher/Release protocol/GitHub Actions workflow가 모두 monorepo에 구현되어 있습니다.

남은 검증은 새 GitHub repository, Neon credential, Steam refresh token을 연결한 뒤 hosted Actions에서 `check -> sync -> legacy -> build -> release` 전체 흐름과 실제 게임 install/remove를 실행하는 운영 integration입니다. 설정 절차는 [`OPERATIONS.md`](OPERATIONS.md)를 참조합니다.

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
- `source_texts`
- `translations`
- `translation_history`
- `builds`
- `build_files`

원칙:

- 원문 revision history 보존
- 번역은 원문 sync 시 삭제하지 않음
- source fingerprint로 source change 계산
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

Workflow 분리:

1. `check-game.yml`: 외부 scheduler가 dispatch하는 revision check + 새 revision `-pre` orchestration
2. `sync-game.yml`: catalog resolve/download/extract/DB sync
3. `build-patch.yml`: reusable snapshot/build/validate (`release`/`develop` distribution channel)
4. `release-patch.yml`: reusable immutable Patch Release + release index 갱신
5. `release.yml`: 입력값 없이 최신 processed revision의 정식 `-release` 수동 build/release
6. `build-patcher.yml`: patcher build/release

운영 흐름:

```text
revision changed
  -> resolve catalog
  -> fetch only required bundles
  -> extract sources
  -> Neon sync
  -> translation snapshot
  -> approved translations only
  -> release-channel `-pre` patch
  -> validate
  -> manifest
  -> GitHub Release
```

번역 승인 자체는 release를 트리거하지 않는다. 번역 완료 시 운영자가 `Release Patch`를 수동 실행해 같은 revision의 `-release`를 만들고 release index의 `release` entry를 교체한다. 개발 검증용 `develop` channel도 승인된 번역만 사용한다.

## 5. DB 동기화 규칙

- 동일 key + 동일 source fingerprint: 기존 번역 그대로 사용
- 동일 key + 변경 fingerprint: 기존 번역 보존, `needs_review`를 계산
- 신규 key: untranslated
- 최신 revision에 없는 key: historical data는 유지하되 현재 revision에서는 retired/missing으로 계산
- 빌드 도중 DB 변경 영향을 막기 위해 build 시작 시 immutable translation snapshot 생성

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


## INT_ANDROID Android 내장 패처

- Windows AstralAutoPatcher의 INT_ANDROID/ADB 로직은 두지 않음.
- Google Play에서 `apkeep`으로 APK/split APK를 취득하고 APKEditor로 병합한 뒤 `assets/bin/Data/data.unity3d` legacy TTF를 빌드 시 교체.
- 기존 게임 launcher 앞에 `com.astralpatch.runtime.BootstrapActivity`를 두고 runtime DEX를 APK에 주입.
- Addressables patch release는 LANG/STR/TMP bundle만 포함하고 각 entry에 source SHA-256/size를 기록.
- 최초 설치 또는 인게임 resource update 중에는 게임 자체 다운로드를 우선하고, 다운로드 완료 후 다음 실행에서 Unity 시작 전에 patch.
- APK가 바뀌지 않는 catalog-only update는 APK 재배포 없이 새 INT_ANDROID patch release만 생성.
- Android APK는 저장소에 보관한 고정 private key가 아니라 Actions Secrets/로컬 `.secrets`의 장기 signing key로 계속 서명.
