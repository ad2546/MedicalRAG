---
tags: [fix, asyncio, sqlalchemy, bug]
---

# Fix — Session Race Condition

> **Found on the very first test run. Fixed in one commit.**

---

## The Bug

`pipeline._write_audit()` received a `db: AsyncSession` parameter — the same session that FastAPI injected for the HTTP request.

```python
# BROKEN
asyncio.create_task(
    self._write_audit(db, case_id, ...)   # <-- reuses request session
)
return response   # FastAPI closes db session HERE
```

When FastAPI returned the response, it called `await db.close()`. The background task then tried to use the closed session:

```
sqlalchemy.exc.IllegalStateChangeError:
Method 'close()' can't be called here;
method '_connection_for_bind()' is already in progress
```

---

## The Fix

`_write_audit` opens its own session:

```python
# FIXED
async def _write_audit(self, case_id, user_id, trace_id, ...):
    async with AsyncSessionLocal() as session:
        audit = PipelineAudit(...)
        session.add(audit)
        await session.commit()
```

No `db` parameter. Independent session lifecycle. Session opens and closes within the method.

---

## Lesson

> **Never pass request-scoped resources (DB sessions, file handles) to background tasks.**

FastAPI manages session lifecycle to the HTTP response boundary. Background tasks live beyond that boundary.

---

## Related

- [[architecture/Pipeline|Pipeline]] — where this runs
- [[architecture/Database|Database]] — session management
- [[fixes/Task GC Fix|Task GC Fix]] — another background task bug

---

*[[🏠 Home|← Home]]*
