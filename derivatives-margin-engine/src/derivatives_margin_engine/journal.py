"""SQLite-backed double-entry cash/collateral journal, settlement replay, and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import platform
import sqlite3
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.errors import (
    DuplicateCollateralError,
    DuplicateMarginCallError,
    ReconciliationError,
    SettlementCorrectionError,
)
from derivatives_margin_engine.models import (
    CallType,
    CollateralLot,
    JournalEntry,
    MarginCall,
    MarginPolicy,
    NettingSet,
    Position,
    RunResult,
    ScenarioSet,
    SettlementPrice,
)

ZERO = Decimal("0.00")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    valuation_date TEXT NOT NULL,
    valuation_time_utc TEXT NOT NULL,
    revision INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    scenario_set_id TEXT NOT NULL,
    scenario_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    seed INTEGER,
    is_settlement_correction INTEGER NOT NULL DEFAULT 0,
    runtime_env_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settlement_snapshots (
    valuation_date TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    price TEXT NOT NULL,
    currency TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (valuation_date, instrument_id, revision),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS collateral_allocations (
    valuation_date TEXT NOT NULL,
    revision INTEGER NOT NULL,
    lot_id TEXT NOT NULL,
    netting_set_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    market_value_base TEXT NOT NULL,
    post_haircut_value_base TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (valuation_date, revision, lot_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS margin_calls (
    call_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    netting_set_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    call_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    reference_call_id TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    netting_set_id TEXT NOT NULL,
    call_id TEXT,
    account TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    debit TEXT NOT NULL,
    credit TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    reference TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def runtime_environment_metadata() -> Dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "executable": sys.executable,
    }


def _make_entry_id(run_id: str, seq: int, salt: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{seq}|{salt}".encode("utf-8")).hexdigest()[:14].upper()
    return f"JE-{digest}"


@dataclass(frozen=True)
class PersistOutcome:
    run_id: str
    status: str  # "COMMITTED" | "IDEMPOTENT_REPLAY"
    margin_calls_posted: int
    journal_entries_posted: int
    duplicate_calls_blocked: int


@dataclass(frozen=True)
class ReconciliationReport:
    is_reconciled: bool
    total_runs: int
    total_margin_calls: int
    total_journal_entries: int
    currency_totals: Dict[str, Dict[str, str]]
    unreconciled_calls: List[str]
    duplicate_collateral_violations: List[str]
    account_balances: List[Dict[str, str]]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_reconciled": self.is_reconciled,
            "total_runs": self.total_runs,
            "total_margin_calls": self.total_margin_calls,
            "total_journal_entries": self.total_journal_entries,
            "currency_totals": self.currency_totals,
            "unreconciled_calls": self.unreconciled_calls,
            "duplicate_collateral_violations": self.duplicate_collateral_violations,
            "account_balances": self.account_balances,
            "errors": self.errors,
        }


class MarginJournalStore:
    """SQLite persistence and double-entry reconciliation store for daily margin settlement."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MarginJournalStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _journal_entries_for_call(
        self,
        run_id: str,
        call: MarginCall,
        seq_start: int,
    ) -> List[JournalEntry]:
        """Build balanced double-entry journal postings for a MarginCall."""
        amt = call.amount
        ccy = call.currency
        if call.call_type == CallType.IM_CALL:
            debit_acct, credit_acct = "IM_MARGIN_RECEIVABLE", "IM_REQUIREMENT_CLEARING"
            asset_id = f"{ccy}_IM_CALL"
        elif call.call_type == CallType.VM_CALL:
            debit_acct, credit_acct = "VM_CASH_RECEIVABLE", "VM_SETTLEMENT_CLEARING"
            asset_id = f"{ccy}_CASH"
        elif call.call_type == CallType.VM_CREDIT:
            debit_acct, credit_acct = "VM_SETTLEMENT_CLEARING", "VM_CASH_PAYABLE"
            asset_id = f"{ccy}_CASH"
        elif call.call_type == CallType.SETTLEMENT_CORRECTION:
            if "CREDIT" in call.reason:
                debit_acct, credit_acct = "VM_SETTLEMENT_CLEARING", "VM_CORRECTION_PAYABLE"
            else:
                debit_acct, credit_acct = "VM_CORRECTION_RECEIVABLE", "VM_SETTLEMENT_CLEARING"
            asset_id = f"{ccy}_CASH"
        else:
            raise ValueError(f"Unsupported CallType: {call.call_type}")

        ref = f"{call.call_type.value}:{call.call_id}"
        return [
            JournalEntry(
                entry_id=_make_entry_id(run_id, seq_start, f"{call.call_id}:DR"),
                run_id=run_id,
                valuation_date=call.valuation_date,
                netting_set_id=call.netting_set_id,
                call_id=call.call_id,
                account=debit_acct,
                asset_id=asset_id,
                currency=ccy,
                debit=amt,
                credit=ZERO,
                entry_type=call.call_type.value,
                reference=ref,
            ),
            JournalEntry(
                entry_id=_make_entry_id(run_id, seq_start + 1, f"{call.call_id}:CR"),
                run_id=run_id,
                valuation_date=call.valuation_date,
                netting_set_id=call.netting_set_id,
                call_id=call.call_id,
                account=credit_acct,
                asset_id=asset_id,
                currency=ccy,
                debit=ZERO,
                credit=amt,
                entry_type=call.call_type.value,
                reference=ref,
            ),
        ]

    def record_run(
        self,
        run_result: RunResult,
        settlement_prices: Sequence[SettlementPrice],
        allow_idempotent_rerun: bool = False,
    ) -> PersistOutcome:
        """Persist a margin run atomically with duplicate-call and correction-ordering guards."""
        cur = self._conn.cursor()

        # 1. Check settlement correction lineage
        if run_result.is_settlement_correction:
            if run_result.revision < 2:
                raise SettlementCorrectionError(
                    "INVALID_SETTLEMENT_CORRECTION",
                    f"Settlement correction requires revision >= 2, got {run_result.revision}",
                )
            prev = cur.execute(
                "SELECT run_id FROM runs WHERE valuation_date = ? AND revision = ?",
                (run_result.valuation_date.isoformat(), run_result.revision - 1),
            ).fetchone()
            if prev is None:
                raise SettlementCorrectionError(
                    "MISSING_PRIOR_REVISION",
                    f"Cannot apply settlement correction rev {run_result.revision} for "
                    f"{run_result.valuation_date.isoformat()}: revision {run_result.revision - 1} not found",
                )

        # 2. Check idempotent exact rerun
        existing_run = cur.execute(
            "SELECT run_id, input_hash, policy_hash, scenario_hash FROM runs WHERE run_id = ?",
            (run_result.run_id,),
        ).fetchone()
        if existing_run is not None:
            if allow_idempotent_rerun and (
                existing_run["input_hash"] == run_result.input_hash
                and existing_run["policy_hash"] == run_result.policy_hash
                and existing_run["scenario_hash"] == run_result.scenario_hash
            ):
                blocked = sum(len(ns.margin_calls) for ns in run_result.netting_set_results)
                return PersistOutcome(
                    run_id=run_result.run_id,
                    status="IDEMPOTENT_REPLAY",
                    margin_calls_posted=0,
                    journal_entries_posted=0,
                    duplicate_calls_blocked=blocked,
                )
            # Duplicate attempt with strict mode -> raise explicit DuplicateMarginCallError if calls exist
            for ns_res in run_result.netting_set_results:
                for call in ns_res.margin_calls:
                    raise DuplicateMarginCallError(call.call_id, call.idempotency_key)
            raise DuplicateMarginCallError(run_result.run_id, run_result.run_id)

        # 3. Check for duplicate margin call idempotency keys before mutating
        all_calls: List[MarginCall] = [
            c for ns in run_result.netting_set_results for c in ns.margin_calls
        ]
        for call in all_calls:
            dup = cur.execute(
                "SELECT call_id FROM margin_calls WHERE idempotency_key = ?",
                (call.idempotency_key,),
            ).fetchone()
            if dup is not None:
                raise DuplicateMarginCallError(call.call_id, call.idempotency_key)

        # 4. Atomic transaction: persist run, prices, collateral snapshot, calls, and journal
        try:
            cur.execute("BEGIN")
            cur.execute(
                """
                INSERT INTO runs (
                    run_id, valuation_date, valuation_time_utc, revision,
                    policy_id, policy_hash, scenario_set_id, scenario_hash,
                    input_hash, seed, is_settlement_correction, runtime_env_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_result.run_id,
                    run_result.valuation_date.isoformat(),
                    run_result.valuation_time_utc,
                    run_result.revision,
                    run_result.policy_id,
                    run_result.policy_hash,
                    run_result.scenario_set_id,
                    run_result.scenario_hash,
                    run_result.input_hash,
                    run_result.seed,
                    1 if run_result.is_settlement_correction else 0,
                    json.dumps(runtime_environment_metadata(), sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            for sp in sorted(settlement_prices, key=lambda s: s.instrument_id):
                cur.execute(
                    """
                    INSERT INTO settlement_snapshots (
                        valuation_date, instrument_id, revision, price, currency, run_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sp.valuation_date.isoformat(),
                        sp.instrument_id,
                        run_result.revision,
                        str(sp.price),
                        sp.currency,
                        run_result.run_id,
                    ),
                )

            entries: List[JournalEntry] = []
            seq = 1
            for ns_res in run_result.netting_set_results:
                for col in ns_res.collateral_valuations:
                    try:
                        cur.execute(
                            """
                            INSERT INTO collateral_allocations (
                                valuation_date, revision, lot_id, netting_set_id,
                                security_id, asset_class, currency, market_value_base,
                                post_haircut_value_base, status, run_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_result.valuation_date.isoformat(),
                                run_result.revision,
                                col.lot_id,
                                col.netting_set_id,
                                col.security_id,
                                col.asset_class,
                                col.base_currency,
                                str(col.market_value_base),
                                str(col.post_haircut_value_base),
                                col.status.value,
                                run_result.run_id,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise DuplicateCollateralError(col.lot_id, [col.netting_set_id]) from exc

                    # Record eligible collateral custody memo entry (balanced DR/CR)
                    if col.post_haircut_value_base > ZERO:
                        entries.append(
                            JournalEntry(
                                entry_id=_make_entry_id(run_result.run_id, seq, f"COL:{col.lot_id}:DR"),
                                run_id=run_result.run_id,
                                valuation_date=run_result.valuation_date,
                                netting_set_id=ns_res.netting_set_id,
                                call_id=None,
                                account="COLLATERAL_CUSTODY_POST_HAIRCUT",
                                asset_id=col.lot_id,
                                currency=col.base_currency,
                                debit=col.post_haircut_value_base,
                                credit=ZERO,
                                entry_type="COLLATERAL_SNAPSHOT",
                                reference=f"LOT:{col.lot_id}:rev{run_result.revision}",
                            )
                        )
                        entries.append(
                            JournalEntry(
                                entry_id=_make_entry_id(run_result.run_id, seq + 1, f"COL:{col.lot_id}:CR"),
                                run_id=run_result.run_id,
                                valuation_date=run_result.valuation_date,
                                netting_set_id=ns_res.netting_set_id,
                                call_id=None,
                                account="COLLATERAL_PLEDGE_LIABILITY",
                                asset_id=col.lot_id,
                                currency=col.base_currency,
                                debit=ZERO,
                                credit=col.post_haircut_value_base,
                                entry_type="COLLATERAL_SNAPSHOT",
                                reference=f"LOT:{col.lot_id}:rev{run_result.revision}",
                            )
                        )
                        seq += 2

                for call in ns_res.margin_calls:
                    cur.execute(
                        """
                        INSERT INTO margin_calls (
                            call_id, idempotency_key, run_id, valuation_date,
                            netting_set_id, account_id, call_type, currency,
                            amount, revision, reason, reference_call_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            call.call_id,
                            call.idempotency_key,
                            run_result.run_id,
                            call.valuation_date.isoformat(),
                            call.netting_set_id,
                            call.account_id,
                            call.call_type.value,
                            call.currency,
                            str(call.amount),
                            call.revision,
                            call.reason,
                            call.reference_call_id,
                        ),
                    )
                    call_entries = self._journal_entries_for_call(run_result.run_id, call, seq)
                    entries.extend(call_entries)
                    seq += len(call_entries)

            for je in entries:
                cur.execute(
                    """
                    INSERT INTO journal_entries (
                        entry_id, run_id, valuation_date, netting_set_id,
                        call_id, account, asset_id, currency,
                        debit, credit, entry_type, reference
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        je.entry_id,
                        je.run_id,
                        je.valuation_date.isoformat(),
                        je.netting_set_id,
                        je.call_id,
                        je.account,
                        je.asset_id,
                        je.currency,
                        str(je.debit),
                        str(je.credit),
                        je.entry_type,
                        je.reference,
                    ),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return PersistOutcome(
            run_id=run_result.run_id,
            status="COMMITTED",
            margin_calls_posted=len(all_calls),
            journal_entries_posted=len(entries),
            duplicate_calls_blocked=0,
        )

    def settle_margin_call(self, call_id: str, settlement_date: date) -> List[JournalEntry]:
        """Post cash/collateral settlement clearing entries against an issued margin call."""
        cur = self._conn.cursor()
        row = cur.execute(
            "SELECT * FROM margin_calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        if row is None:
            raise ReconciliationError(
                "UNKNOWN_MARGIN_CALL",
                f"Cannot settle unknown margin call '{call_id}'",
            )
        already = cur.execute(
            "SELECT 1 FROM journal_entries WHERE call_id = ? AND entry_type = 'CALL_SETTLEMENT'",
            (call_id,),
        ).fetchone()
        if already is not None:
            raise DuplicateMarginCallError(call_id, f"SETTLEMENT|{call_id}")

        amt = Decimal(row["amount"])
        ccy = row["currency"]
        call_type = row["call_type"]
        if call_type == CallType.IM_CALL.value:
            debit_acct, credit_acct = "CASH_COLLATERAL_IM", "IM_MARGIN_RECEIVABLE"
        elif call_type == CallType.VM_CALL.value:
            debit_acct, credit_acct = "CASH_SETTLEMENT_ACCOUNT", "VM_CASH_RECEIVABLE"
        elif call_type == CallType.VM_CREDIT.value:
            debit_acct, credit_acct = "VM_CASH_PAYABLE", "CASH_SETTLEMENT_ACCOUNT"
        else:
            if "CREDIT" in row["reason"]:
                debit_acct, credit_acct = "VM_CORRECTION_PAYABLE", "CASH_SETTLEMENT_ACCOUNT"
            else:
                debit_acct, credit_acct = "CASH_SETTLEMENT_ACCOUNT", "VM_CORRECTION_RECEIVABLE"

        entries = [
            JournalEntry(
                entry_id=_make_entry_id(row["run_id"], 9001, f"SETTLE:{call_id}:DR"),
                run_id=row["run_id"],
                valuation_date=settlement_date,
                netting_set_id=row["netting_set_id"],
                call_id=call_id,
                account=debit_acct,
                asset_id=f"{ccy}_CASH",
                currency=ccy,
                debit=amt,
                credit=ZERO,
                entry_type="CALL_SETTLEMENT",
                reference=f"SETTLE:{call_id}",
            ),
            JournalEntry(
                entry_id=_make_entry_id(row["run_id"], 9002, f"SETTLE:{call_id}:CR"),
                run_id=row["run_id"],
                valuation_date=settlement_date,
                netting_set_id=row["netting_set_id"],
                call_id=call_id,
                account=credit_acct,
                asset_id=f"{ccy}_CASH",
                currency=ccy,
                debit=ZERO,
                credit=amt,
                entry_type="CALL_SETTLEMENT",
                reference=f"SETTLE:{call_id}",
            ),
        ]
        try:
            cur.execute("BEGIN")
            for je in entries:
                cur.execute(
                    """
                    INSERT INTO journal_entries (
                        entry_id, run_id, valuation_date, netting_set_id,
                        call_id, account, asset_id, currency,
                        debit, credit, entry_type, reference
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        je.entry_id,
                        je.run_id,
                        je.valuation_date.isoformat(),
                        je.netting_set_id,
                        je.call_id,
                        je.account,
                        je.asset_id,
                        je.currency,
                        str(je.debit),
                        str(je.credit),
                        je.entry_type,
                        je.reference,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return entries

    def list_margin_calls(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM margin_calls ORDER BY valuation_date, revision, netting_set_id, call_type"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_settlement_snapshots(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM settlement_snapshots ORDER BY valuation_date, instrument_id, revision"
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_journal(self, raise_on_failure: bool = False) -> ReconciliationReport:
        """Verify double-entry balance, 100% margin call reconciliation, and zero duplicate collateral."""
        cur = self._conn.cursor()
        errors: List[str] = []

        total_runs = cur.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        calls = cur.execute("SELECT * FROM margin_calls").fetchall()
        entries = cur.execute("SELECT * FROM journal_entries").fetchall()

        # 1. Currency-level double-entry balance
        ccy_dr: Dict[str, Decimal] = {}
        ccy_cr: Dict[str, Decimal] = {}
        account_agg: Dict[Tuple[str, str, str], Tuple[Decimal, Decimal]] = {}
        call_entry_dr: Dict[str, Decimal] = {}
        call_entry_cr: Dict[str, Decimal] = {}
        valid_call_ids = {c["call_id"] for c in calls}

        for je in entries:
            ccy = je["currency"]
            dr = Decimal(je["debit"])
            cr = Decimal(je["credit"])
            ccy_dr[ccy] = ccy_dr.get(ccy, ZERO) + dr
            ccy_cr[ccy] = ccy_cr.get(ccy, ZERO) + cr

            key = (je["netting_set_id"], je["account"], ccy)
            prev_dr, prev_cr = account_agg.get(key, (ZERO, ZERO))
            account_agg[key] = (prev_dr + dr, prev_cr + cr)

            cid = je["call_id"]
            if cid is not None:
                if cid not in valid_call_ids:
                    errors.append(f"Orphan journal entry '{je['entry_id']}' references unknown call '{cid}'")
                if je["entry_type"] != "CALL_SETTLEMENT":
                    call_entry_dr[cid] = call_entry_dr.get(cid, ZERO) + dr
                    call_entry_cr[cid] = call_entry_cr.get(cid, ZERO) + cr

        currency_totals: Dict[str, Dict[str, str]] = {}
        for ccy in sorted(set(ccy_dr.keys()) | set(ccy_cr.keys())):
            dr_tot = ccy_dr.get(ccy, ZERO)
            cr_tot = ccy_cr.get(ccy, ZERO)
            diff = dr_tot - cr_tot
            currency_totals[ccy] = {
                "total_debit": str(dr_tot),
                "total_credit": str(cr_tot),
                "imbalance": str(diff),
            }
            if diff != ZERO:
                errors.append(f"Journal imbalance for currency {ccy}: DR={dr_tot} != CR={cr_tot}")

        # 2. Every margin call reconciles 1:1 to journal postings
        unreconciled_calls: List[str] = []
        for c in calls:
            cid = c["call_id"]
            expected = Decimal(c["amount"])
            dr_posted = call_entry_dr.get(cid, ZERO)
            cr_posted = call_entry_cr.get(cid, ZERO)
            if dr_posted != expected or cr_posted != expected:
                unreconciled_calls.append(cid)
                errors.append(
                    f"Margin call {cid} amount {expected} != journal DR={dr_posted}, CR={cr_posted}"
                )

        # 3. No double-counted collateral per (valuation_date, revision, lot_id)
        dup_rows = cur.execute(
            """
            SELECT valuation_date, revision, lot_id, COUNT(*) AS cnt
            FROM collateral_allocations
            GROUP BY valuation_date, revision, lot_id
            HAVING cnt > 1
            """
        ).fetchall()
        dup_violations = [
            f"{r['valuation_date']}:rev{r['revision']}:{r['lot_id']} (cnt={r['cnt']})"
            for r in dup_rows
        ]
        for v in dup_violations:
            errors.append(f"Duplicate collateral allocation detected: {v}")

        account_balances = [
            {
                "netting_set_id": ns_id,
                "account": acct,
                "currency": ccy,
                "debit": str(dr),
                "credit": str(cr),
                "net_balance": str(dr - cr),
            }
            for (ns_id, acct, ccy), (dr, cr) in sorted(account_agg.items())
        ]

        report = ReconciliationReport(
            is_reconciled=len(errors) == 0,
            total_runs=total_runs,
            total_margin_calls=len(calls),
            total_journal_entries=len(entries),
            currency_totals=currency_totals,
            unreconciled_calls=unreconciled_calls,
            duplicate_collateral_violations=dup_violations,
            account_balances=account_balances,
            errors=errors,
        )
        if raise_on_failure and not report.is_reconciled:
            raise ReconciliationError(
                "JOURNAL_RECONCILIATION_FAILED",
                f"Journal reconciliation failed with {len(errors)} error(s)",
                {"errors": errors},
            )
        return report

    def replay_schedule(
        self,
        schedule_payload: Mapping[str, Any],
        base_policy: MarginPolicy,
        scenario_set: ScenarioSet,
        allow_idempotent_rerun: bool = False,
        settle_calls: bool = False,
    ) -> Tuple[List[Dict[str, Any]], ReconciliationReport]:
        """Replay a multi-day settlement schedule and reconcile the resulting SQLite journal."""
        netting_sets = [NettingSet.from_dict(ns) for ns in schedule_payload["netting_sets"]]
        day_summaries: List[Dict[str, Any]] = []

        for day in schedule_payload["days"]:
            overrides = day.get("policy_overrides", {})
            day_policy = base_policy.with_haircut_overrides(overrides.get("haircut_overrides", {}))
            val_date = date.fromisoformat(day["valuation_date"])
            revision = int(day.get("revision", 1))
            is_corr = bool(day.get("is_settlement_correction", False))
            positions = [Position.from_dict(p) for p in day["positions"]]
            prices = [SettlementPrice.from_dict(s) for s in day["settlement_prices"]]
            collateral = [CollateralLot.from_dict(c) for c in day["collateral_inventory"]]

            run_res = evaluate_margin_run(
                valuation_date=val_date,
                policy=day_policy,
                scenario_set=scenario_set,
                netting_sets=netting_sets,
                positions=positions,
                settlement_prices=prices,
                collateral_inventory=collateral,
                revision=revision,
                valuation_time_utc=day.get("valuation_time_utc"),
                is_settlement_correction=is_corr,
            )
            outcome = self.record_run(
                run_res,
                prices,
                allow_idempotent_rerun=allow_idempotent_rerun,
            )
            if settle_calls and outcome.status == "COMMITTED":
                for ns_res in run_res.netting_set_results:
                    for call in ns_res.margin_calls:
                        self.settle_margin_call(call.call_id, val_date)

            day_summaries.append(
                {
                    "day_label": day.get("day_label", val_date.isoformat()),
                    "persist_status": outcome.status,
                    "margin_calls_posted": outcome.margin_calls_posted,
                    "journal_entries_posted": outcome.journal_entries_posted,
                    "duplicate_calls_blocked": outcome.duplicate_calls_blocked,
                    "run_result": run_res.to_dict(),
                }
            )

        reconciliation = self.reconcile_journal(raise_on_failure=True)
        return day_summaries, reconciliation
