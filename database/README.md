# Database

Neon PostgreSQL의 schema와 migration을 관리합니다.

## 원칙

- 운영 DB를 Neon Console에서 직접 변경하지 않습니다.
- 모든 schema 변경은 `migrations/`에 순서가 있는 SQL migration으로 기록합니다.
- Builder source sync는 원문 revision history만 추가하며 기존 번역을 삭제하거나 덮어쓰지 않습니다.
- 번역 유효성은 `translations.source_fingerprint`와 현재 `source_texts.source_fingerprint` 비교로 계산합니다.
- Patcher는 DB에 접속하지 않습니다.

## 연결

GitHub Actions runtime은 `DATABASE_URL`(pooled)을 사용합니다. Migration처럼 session semantics가 필요한 작업은 `DATABASE_URL_DIRECT`를 사용합니다.

로컬 개발 및 CI 테스트는 실제 Neon credential을 요구하지 않습니다. 실제 DB integration test는 별도 ephemeral Neon branch에서 실행하도록 구성할 예정입니다.

## Migration

현재 migration은 순수 SQL이며 파일명 순서대로 한 번씩 적용합니다.

```text
0001_initial.sql
```

초기 schema는 다음 테이블을 정의합니다.

- `game_revisions`
- `asset_locations`
- `translation_units`
- `source_texts`
- `translations`
- `translation_history`
- `builds`
- `build_files`
