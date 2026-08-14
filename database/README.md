# Database

Neon PostgreSQL의 canonical 원문, 승인 번역, 번역 검토 기록, build metadata를 관리합니다.

## 핵심 원칙

1. `INT_STEAM`만 번역 원문을 제공합니다.
2. `game_revisions`의 한 revision이 원문 변경 기록의 그룹입니다.
3. 원문 텍스트는 `source_versions`에 **새 fingerprint가 생겼을 때만** 저장합니다.
4. `source_changes`에는 해당 revision에서 실제로 추가/변경/삭제된 unit만 기록하고 즉시 `applied` 상태로 둡니다.
5. `translation_units.current_source_version_id`가 현재 적용 원문을 가리킵니다.
6. `translations`에는 승인된 production 번역만 존재하며 status 컬럼이 없습니다.
7. 새 번역/수정은 반드시 `translation_changes`에 `pending`으로 먼저 생성합니다.
8. 승인 시 하나의 transaction 안에서 change를 `approved`로 바꾸고 `translations`를 갱신합니다.
9. 대기/반려/superseded change는 patch 결과에 영향을 주지 않습니다.
10. Patcher는 Neon에 직접 접속하지 않습니다.

## 테이블 관계

```text
game_revisions                         <- INT_STEAM revision = 원문 변경 그룹
  └─ source_changes
       ├─ old_source_version_id
       └─ new_source_version_id

translation_units
  ├─ current_source_version_id ───────> source_versions
  ├─ translations                     <- 현재 승인된 production 값 0..1 / locale
  └─ translation_changes              <- pending/approved/rejected/superseded 이력
       └─ translation_change_groups

asset_locations
builds
  └─ build_files
```

### `source_versions`

`UNIQUE(unit_id, source_fingerprint)`입니다. 동일한 원문은 게임 revision이 바뀌어도 다시 저장하지 않습니다. 과거 원문으로 되돌아간 경우에도 기존 source version을 재사용합니다.

### `source_changes`

`PRIMARY KEY(revision_id, unit_id)`이며 `added`, `modified`, `removed` 중 하나입니다. `status`는 현재 `applied`만 허용합니다. 즉 게임에서 탐지한 원문 변경은 검토 대기 대상이 아니라 외부 사실로 즉시 현재 원문에 반영됩니다.

### `translations`

이 테이블은 production projection입니다.

```text
unit_id + locale -> 승인된 현재 번역 하나
```

`status`, `draft`, `needs_review` 같은 개념은 없습니다. DB trigger가 `applied_change_id`가 같은 unit/locale/text/source를 가진 **approved `translation_changes` row**인지 확인하므로 웹사이트나 다른 클라이언트가 승인 절차를 우회해 production 번역을 넣는 것을 방지합니다.

원문이 변경되어도 기존 승인 번역을 자동 폐기하지 않습니다. 새 번역이 `pending`이면 기존 승인 번역을 계속 사용하고, 기존 승인값이 없으면 원문을 사용합니다.

### `translation_changes`

웹 번역 편집기의 audit/work queue입니다.

```text
pending -> approved
        -> rejected
        -> superseded
```

한 변경을 승인하면 같은 unit/locale에 남아 있던 다른 pending 제안은 `superseded` 처리됩니다. 제안이 만들어진 뒤 원문 source version이 바뀌면 해당 제안은 승인할 수 없으며 새 현재 원문을 기준으로 다시 제안해야 합니다.

### 웹사이트용 View

- `current_source_texts`: 현재 활성 원문만 평탄화해서 제공.
- `translation_workbench`: 현재 원문 + 승인 번역 + 가장 최근 change를 한 화면에서 조회할 수 있도록 제공. `approved_for_current_source`로 승인 번역이 현 원문 버전에서 승인된 값인지 표시하지만, false여도 production 번역은 유지됩니다.
- `source_revision_change_summary`: revision별 added/modified/removed 개수.
- `translation_change_group_summary`: 번역 변경 그룹별 pending/approved/rejected/superseded 개수.

## Migration

2026-08 DB 초기화와 함께 migration history를 새 baseline으로 squash했습니다.

```text
0001_initial.sql
```

기존 DB에는 이전 `0001` checksum이 기록되어 있으므로 이 commit 적용 후 **기존 DB를 그대로 migration하면 의도적으로 실패합니다.** [`../docs/RESET.md`](../docs/RESET.md)의 backup/reset/repopulate 절차를 수행해야 합니다.

## 연결

- Actions runtime: `NEON_DATABASE_URL` / `DATABASE_URL`
- migration/reset: `NEON_DATABASE_URL_DIRECT` / `DATABASE_URL_DIRECT`

승인 번역 backup 파일은 `tools/database/translation_backup.py`로 export/import할 수 있습니다.
