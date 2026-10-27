"""ISMS document templates prefilled from the repository (scripts/assurance-pack.py isms-kit).

Four Markdown documents cover the manual evidence the control catalog asks for:
the information security policy, the risk register, the continuity and recovery
plan and the supplier register. Platform facts come from
k8s/files/compliance-controls.yaml, the backup coverage table in
docs/operations.md and docs/guides/external-services.md; what only the customer
knows reads "[to complete]". Each document ends with an approval block that
approvals() reads back from signed copies for the evidence pack.
"""
from pathlib import Path
import hashlib
import re

TO_COMPLETE = "[to complete]"
DOCUMENTS = {
    "information-security-policy.md": "Information security policy",
    "risk-register.md": "Risk register",
    "continuity-and-recovery-plan.md": "Continuity and recovery plan",
    "supplier-register.md": "Supplier register",
}
APPROVAL = re.compile(r"^- (Approved by|Approved on|Next review): *(.*)$", re.M)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(headers, rows):
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + [
        "| " + " | ".join(cell(value) for value in row) + " |" for row in rows]


def coverage(operations_text):
    """Rows of the backup coverage table: data, protection, recovery point, notes."""
    section = operations_text.split("### Coverage and recovery points", 1)[1].split("\n### ", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [value.strip() for value in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and cells[0] != "Data" and not set("".join(cells)) <= set("-: "):
            rows.append(cells)
    return rows


def plain(text):
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


def approval_block():
    return ["", "## Approval", "", f"- Approved by: {TO_COMPLETE}", f"- Approved on: {TO_COMPLETE}",
            f"- Next review: {TO_COMPLETE}", ""]


def header(title, generated):
    return [f"# {title}", "", f"Template generated {generated} from the Swirl Cloud repository. Replace every "
            f"`{TO_COMPLETE}`, adapt the text to the organisation, and keep the signed copy with the evidence.", ""]


def policy(catalog, generated):
    frameworks = catalog["frameworks"]
    lines = header("Information security policy", generated) + [
        "## Scope and responsibilities", "",
        f"- Organisation: {TO_COMPLETE}", f"- Scope: the Swirl Cloud platform, its hosts and the applications it runs; {TO_COMPLETE}",
        f"- Security owner: {TO_COMPLETE}", f"- Management approval and review cycle: {TO_COMPLETE}", "",
        "## Measures", "", "The organisation applies the following measures. Each is checked automatically or by the "
        "documents named in the evidence pack (`./install.sh --compliance --evidence-pack`).", ""]
    for control in catalog["controls"]:
        mapped = "; ".join(f"{frameworks[key]} {', '.join(map(str, values))}" for key, values in control.get("frameworks", {}).items())
        lines += [f"### {control['title']}", "", control["objective"], "",
                  f"Verified by: {'automated checks' if control.get('checks') else 'documents'}. Requirements: {mapped or '-'}.", ""]
    return "\n".join(lines + approval_block())


def risk_register(catalog, coverage_rows, generated):
    rows = [[f"R{number:02d}", f"Control lapses: {control['title']}", f"`{control['id']}`: {control['objective']}",
             TO_COMPLETE, TO_COMPLETE, TO_COMPLETE, TO_COMPLETE]
            for number, control in enumerate(catalog["controls"], 1)]
    offset = len(rows)
    gaps = [row for row in coverage_rows if row[2].lower().startswith("none")]
    rows += [[f"R{offset + number:02d}", f"Loss of data: {plain(data)}", f"Not recovered by the backups today: {plain(notes) or plain(protection)}",
              TO_COMPLETE, TO_COMPLETE, TO_COMPLETE, TO_COMPLETE]
             for number, (data, protection, _point, notes) in enumerate(gaps, 1)]
    lines = header("Risk register", generated) + [
        "Rate likelihood and impact on the organisation's scale, choose a treatment (reduce, accept, transfer, avoid) "
        "and name an owner. The first rows follow the platform's controls; the rest are data the backups do not "
        "recover yet (docs/operations.md, coverage and recovery points).", ""]
    lines += markdown_table(["ID", "Risk", "Existing treatment or gap", "Likelihood", "Impact", "Decision", "Owner"], rows)
    return "\n".join(lines + approval_block())


def continuity_plan(coverage_rows, generated):
    lines = header("Continuity and recovery plan", generated) + [
        "## Objectives", "", f"- Recovery time objective for the platform: {TO_COMPLETE}",
        f"- Recovery point objective agreed with the business: {TO_COMPLETE}",
        f"- Critical services and their order of recovery: {TO_COMPLETE}", "",
        "## What the backups recover", "", "From the platform's backup coverage (docs/operations.md):", ""]
    lines += markdown_table(["Data", "Protection", "Recovery point", "Gaps"],
                            [[plain(value) for value in row] for row in coverage_rows])
    lines += ["", "## Procedures", "",
              "- Restore after losing the host: docs/operations.md, \"Restore after losing the host\".",
              "- Recovery kit: exported and stored off the host as docs/operations.md, \"Operator recovery checklist\" describes.",
              "- Exit or migration: docs/guides/exit-plan.md.", "",
              "## Contacts and drills", "", f"- Incident lead and deputy: {TO_COMPLETE}",
              f"- Hosting provider support: {TO_COMPLETE}", f"- Last restore drill (date, duration, gaps): {TO_COMPLETE}",
              f"- Next drill: {TO_COMPLETE}"]
    return "\n".join(lines + approval_block())


def supplier_register(services, generated):
    rows = [[plain(s["service"]), plain(s["provider"]), plain(s["used_for"]) or s["category"], plain(s["data_received"]) or "-",
             TO_COMPLETE, TO_COMPLETE] for s in services]
    lines = header("Supplier register", generated) + [
        "Every outside service the platform uses (docs/guides/external-services.md), plus the organisation's own "
        "suppliers. Record the agreement (contract, terms, data processing agreement) and the last assessment.", ""]
    lines += markdown_table(["Service", "Provider", "Purpose", "Data received", "Agreement", "Last assessment"], rows)
    lines += ["", f"Other suppliers of the organisation: {TO_COMPLETE}"]
    return "\n".join(lines + approval_block())


def render(catalog, operations_text, services, generated):
    rows = coverage(operations_text)
    return {"information-security-policy.md": policy(catalog, generated),
            "risk-register.md": risk_register(catalog, rows, generated),
            "continuity-and-recovery-plan.md": continuity_plan(rows, generated),
            "supplier-register.md": supplier_register(services, generated)}


def approvals(directory):
    """For the evidence pack: each kit document's signed copy in DIRECTORY, who approved it and when."""
    directory = Path(directory)
    result = []
    for name, title in DOCUMENTS.items():
        path = directory / name
        if not path.is_file():
            result.append({"document": title, "file": name, "status": "missing"})
            continue
        text = path.read_text(errors="replace")
        fields = {key: value.strip() for key, value in APPROVAL.findall(text)}
        approved = all(fields.get(key) and fields[key] != TO_COMPLETE for key in ("Approved by", "Approved on"))
        result.append({"document": title, "file": name, "status": "approved" if approved else "not approved",
                       "approved_by": fields.get("Approved by", ""), "approved_on": fields.get("Approved on", ""),
                       "next_review": fields.get("Next review", ""), "unfilled": text.count(TO_COMPLETE),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result
