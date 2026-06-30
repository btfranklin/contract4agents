from __future__ import annotations

from tests.fixtures.ops_desk_lab.data import as_json, execute, fetch_all, fetch_one


def billing_lookup_invoice(account_id: str, invoice_id: str = "") -> str:
    """Look up seeded invoice records for an account."""
    if invoice_id:
        result = fetch_one(
            "SELECT invoice_id, amount, status, summary FROM invoices WHERE account_id = ? AND invoice_id = ?",
            (account_id, invoice_id),
        )
        return as_json({"invoice": result})
    return as_json(
        {
            "invoices": fetch_all(
                "SELECT invoice_id, amount, status, summary FROM invoices WHERE account_id = ? ORDER BY invoice_id",
                (account_id,),
            )
        }
    )


def billing_create_credit(account_id: str, invoice_id: str, amount: float, reason: str) -> str:
    """Create a fake credit for a duplicate or incorrect invoice."""
    result = execute(
        "INSERT INTO credits(account_id, invoice_id, amount) VALUES (?, ?, ?)",
        (account_id, invoice_id, amount),
    )
    return as_json(
        {"credit_id": result["row_id"], "account_id": account_id, "invoice_id": invoice_id, "reason": reason}
    )


def security_audit_log(account_id: str) -> str:
    """Return seeded account security audit events."""
    return as_json(
        {
            "events": fetch_all(
                "SELECT event, summary FROM audit_events WHERE account_id = ? ORDER BY id",
                (account_id,),
            )
        }
    )


def security_lock_account(account_id: str, reason: str) -> str:
    """Lock an account in fake local state."""
    result = execute("INSERT INTO locks(account_id, reason) VALUES (?, ?)", (account_id, reason))
    return as_json({"lock_id": result["row_id"], "account_id": account_id, "reason": reason})


def access_list_permissions(account_id: str) -> str:
    """List current seeded account permissions."""
    return as_json(
        {
            "permissions": fetch_all(
                "SELECT entitlement, status FROM permissions WHERE account_id = ? ORDER BY entitlement",
                (account_id,),
            )
        }
    )


def access_grant_access(account_id: str, entitlement: str) -> str:
    """Grant fake local access to an entitlement."""
    execute(
        "INSERT OR REPLACE INTO permissions(account_id, entitlement, status) VALUES (?, ?, ?)",
        (account_id, entitlement, "enabled"),
    )
    result = execute("INSERT INTO grants(account_id, entitlement) VALUES (?, ?)", (account_id, entitlement))
    return as_json({"grant_id": result["row_id"], "account_id": account_id, "entitlement": entitlement})


def knowledge_search(query: str) -> str:
    """Search the fake local knowledge base."""
    terms = [term for term in query.lower().split() if term]
    rows = []
    for term in terms:
        needle = f"%{term}%"
        rows.extend(
            fetch_all(
                "SELECT slug, title, body FROM articles WHERE lower(title) LIKE ? OR lower(body) LIKE ? ORDER BY slug",
                (needle, needle),
            )
        )
    unique = {row["slug"]: row for row in rows}
    return as_json({"articles": list(unique.values())})
