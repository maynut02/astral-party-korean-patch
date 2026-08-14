-- Revert the temporary one-shot approvals that copied source fallback text into
-- translations only to satisfy the former all-approved release gate.
-- Keeping the rows as draft preserves auditability/history while ensuring they
-- can never be selected as an approved Korean translation.
UPDATE translations
SET status = 'draft',
    updated_at = now(),
    updated_by = 'synthetic-approval-cleanup'
WHERE updated_by = 'one-shot-neon-approve'
  AND status = 'approved';
