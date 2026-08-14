# Database

Neon PostgreSQL schema와 migration을 관리합니다.

## 원칙

- 운영 schema 변경은 `migrations/`의 순차 SQL migration으로만 수행합니다.
- `INT_STEAM`만 canonical translation source이며 새 `source_texts`를 생성합니다.
- `CN_STEAM`과 `INT_ANDROID`는 `game_revisions`/`asset_locations` 호환 metadata만 저장합니다.
- source sync는 기존 translation variant를 삭제하거나 덮어쓰지 않습니다.
- 번역 유효성은 현재 `source_texts.source_fingerprint`와 translation variant의 fingerprint/status/text로 계산합니다.
- Patcher는 Neon credential을 받지 않으며 DB에 직접 접속하지 않습니다.

## 연결

GitHub Actions runtime은 `DATABASE_URL` pooled connection을 사용합니다. Migration은 `DATABASE_URL_DIRECT`를 사용합니다.

로컬/CI 단위 테스트는 실제 Neon credential을 요구하지 않습니다.

## Migration

파일명 순서대로 정확히 한 번 적용하고, 적용된 migration의 SHA-256이 바뀌면 runner가 실패합니다.

```text
0001_initial.sql
0002_rebuildable_builds.sql
0003_release_channels.sql
0004_approve_legacy_imports.sql
0005_translation_variants.sql
0006_revert_synthetic_approvals.sql
```

현재 핵심 테이블:

- `game_revisions`
- `asset_locations`
- `translation_units`
- `source_texts`
- `translations`
- `translation_history`
- `builds`
- `build_files`
- `schema_migrations`
