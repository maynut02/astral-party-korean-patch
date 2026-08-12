# Database

Neon PostgreSQL의 schema와 migration을 관리합니다.

원칙:

- 운영 DB를 콘솔에서 직접 변경하지 않습니다.
- 모든 schema 변경은 `migrations/`에 순서가 있는 migration으로 기록합니다.
- Builder와 번역 편집 계층은 별도의 DB 권한을 가질 수 있도록 설계합니다.
- 실제 initial schema는 Phase 3에서 integration tests와 함께 추가합니다.
