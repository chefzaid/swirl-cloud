#!/usr/bin/env python3
"""Customer assurance documents built from the control catalog.

  assurance-pack.py questionnaire caiq-v4 [--live] [--format markdown|csv|json] [--output-dir DIR]
  assurance-pack.py contract-annex [--generic] [--provider-name NAME ...] [--format markdown|json] [--output-dir DIR]
  assurance-pack.py isms-kit --output-dir DIR

The questionnaire maps each catalog control (k8s/files/compliance-controls.yaml)
to CSA CCM v4 control IDs (config/questionnaires/caiq-v4.yaml) with its evidence:
the automated checks, or "manual" with the documents to attach. --live adds each
control's current status from Prometheus, as ./install.sh --compliance does.

The contract annex lists the technical and organisational measures from the
catalog, the sub-processors from docs/guides/external-services.md, the data and
processing locations from the cluster's nodes, and DORA register-of-information
rows. --generic skips the cluster and leaves locations to complete, for contract
templates. Fields only the provider or customer knows read "[to complete]".

The ISMS kit writes the information security policy, risk register, continuity
and recovery plan and supplier register templates (scripts/lib/isms_kit.py).
Read-only; Kubernetes Secrets are never read.
"""
import argparse
import csv
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "k8s/files/compliance-controls.yaml"
QUESTIONNAIRES = ROOT / "config/questionnaires"
TO_COMPLETE = "[to complete]"
EXIT_PLAN = "Swirl Cloud exit plan (docs/guides/exit-plan.md)"
# External-services sections whose providers receive platform data; download sources only see requests.
SUB_PROCESSOR_SECTIONS = ("Required or selected at installation", "Data shared while running")
CCM_DOMAINS = ("A&A", "AIS", "BCR", "CCC", "CEK", "DCS", "DSP", "GRC", "HRS", "IAM", "IPY", "IVS", "LOG", "SEF",
               "STA", "TVM", "UEM")


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plain(text):
    """Guide text without Markdown links, which would not resolve outside the repository."""
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def ccm_key(identifier):
    domain, _, number = identifier.partition("-")
    return CCM_DOMAINS.index(domain), int(number)


def load_mapping(name, catalog):
    """The questionnaire mapping; every catalog control must appear, with well-formed CCM IDs."""
    mapping = yaml.safe_load((QUESTIONNAIRES / f"{name}.yaml").read_text())
    controls = {control["id"] for control in catalog["controls"]}
    mapped = mapping.get("controls") or {}
    problems = [f"{c}: missing from {name}.yaml" for c in sorted(controls - set(mapped))]
    problems += [f"{c}: not in the catalog" for c in sorted(set(mapped) - controls)]
    for control, identifiers in mapped.items():
        for identifier in identifiers or []:
            domain, _, number = str(identifier).partition("-")
            if domain not in CCM_DOMAINS or not (number.isdigit() and len(number) == 2):
                problems.append(f"{control}: {identifier} is not a CCM v4 control ID")
    if problems:
        raise SystemExit(f"{name} mapping:\n  " + "\n  ".join(problems))
    return mapping


def questionnaire(name, live):
    catalog = yaml.safe_load(CATALOG.read_text())
    mapping = load_mapping(name, catalog)
    statuses = {}
    if live:
        compliance = load_script("compliance-report")
        statuses = {r["id"]: r["status"] for r in compliance.evaluate(catalog, compliance.Prometheus())}
    controls = {control["id"]: control for control in catalog["controls"]}
    rows = {}
    for control_id, identifiers in mapping["controls"].items():
        control = controls[control_id]
        checks = [check["description"] for check in control.get("checks", [])]
        for identifier in identifiers or []:
            rows.setdefault(identifier, []).append({
                "control": control_id, "title": control["title"], "measure": control["objective"],
                "evidence": "automated" if checks else "manual", "checks": checks, "documents": control.get("manual", []),
                **({"status": statuses.get(control_id, "unknown")} if live else {})})
    return {"questionnaire": mapping["questionnaire"], "verified": bool(mapping.get("verified")),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "unmapped_controls": [c for c, ids in mapping["controls"].items() if not ids],
            "sovereignty_report": "not available yet: ./install.sh --sovereignty-report is planned (TODO §33)",
            "questions": [{"ccm_control": identifier, "answers": rows[identifier]} for identifier in sorted(rows, key=ccm_key)]}


def questionnaire_markdown(pack):
    lines = [f"# {pack['questionnaire']} evidence map", "", f"Generated {pack['generated_at']}.", ""]
    if not pack["verified"]:
        lines += ["> **Unverified mapping.** The CCM control IDs were drafted without CSA's machine-readable bundle;"
                  " check each ID and title against the CAIQ version you answer before submitting.", ""]
    lines += ["Each CCM control lists the Swirl Cloud controls that answer it. **Automated** evidence is checked"
              " continuously (`./install.sh --compliance`); **manual** evidence is the documents to attach.", "",
              f"Sovereignty: {pack['sovereignty_report']}.", ""]
    for question in pack["questions"]:
        lines += [f"## {question['ccm_control']}", ""]
        for answer in question["answers"]:
            status = f", status {answer['status']}" if "status" in answer else ""
            lines += [f"- **{answer['title']}** (`{answer['control']}`, {answer['evidence']}{status}): {answer['measure']}"]
            lines += [f"  - Check: {check}" for check in answer["checks"]]
            lines += [f"  - Document: {document}" for document in answer["documents"]]
        lines.append("")
    return "\n".join(lines)


def spreadsheet_safe(value):
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(columns, rows):
    table = io.StringIO()
    writer = csv.DictWriter(table, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: spreadsheet_safe(row.get(key, "")) for key in columns} for row in rows)
    return table.getvalue()


def questionnaire_csv(pack):
    rows = [{"ccm_control": q["ccm_control"], "control": a["control"], "title": a["title"], "evidence": a["evidence"],
             "status": a.get("status", ""), "measure": a["measure"], "checks": "; ".join(a["checks"]),
             "documents": "; ".join(a["documents"]), "mapping": "verified" if pack["verified"] else "unverified"}
            for q in pack["questions"] for a in q["answers"]]
    return to_csv(["ccm_control", "control", "title", "evidence", "status", "measure", "checks", "documents", "mapping"], rows)


def contract_annex(generic, provider):
    catalog = yaml.safe_load(CATALOG.read_text())
    inventory = load_script("asset-inventory")
    measures = [{"control": c["id"], "measure": c["title"], "description": c["objective"],
                 "verification": "automated checks" if c.get("checks") else "documents",
                 "gdpr": c.get("frameworks", {}).get("gdpr", [])} for c in catalog["controls"]]
    services = inventory.external_services(inventory.EXTERNAL_SERVICES.read_text())
    sub_processors = [{"service": plain(s["service"]), "provider": plain(s["provider"]), "purpose": plain(s["used_for"]),
                       "data": plain(s["data_received"]), "when": s["category"]}
                      for s in services if s["category"] in SUB_PROCESSOR_SECTIONS]
    errors, locations = [], []
    if not generic:
        try:
            locations = [{"node": s["name"], "roles": s["roles"], "region": s["region"] or TO_COMPLETE,
                          "zone": s["zone"] or TO_COMPLETE} for s in inventory.servers(inventory.kubectl_items("nodes"))]
        except (RuntimeError, OSError, ValueError, KeyError) as error:
            errors.append(f"locations: {error}")
    regions = sorted({row["region"] for row in locations}) or [TO_COMPLETE]
    register = [{"rank": 1, "provider": provider["name"], "lei": provider["lei"], "country": provider["country"],
                 "service": "Private cloud platform operation (Swirl Cloud)", "service_type": "ICT infrastructure and platform",
                 "functions_supported": TO_COMPLETE, "data_locations": ", ".join(regions),
                 "processing_locations": ", ".join(regions), "subcontractor_of": "", "exit_plan": provider["exit_plan"]}]
    register += [{"rank": 2, "provider": s["provider"], "lei": TO_COMPLETE, "country": TO_COMPLETE, "service": s["service"],
                  "service_type": s["purpose"] or s["when"], "functions_supported": TO_COMPLETE,
                  "data_locations": TO_COMPLETE, "processing_locations": TO_COMPLETE, "subcontractor_of": provider["name"],
                  "exit_plan": provider["exit_plan"]} for s in sub_processors]
    return {"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "generic": generic,
            "provider": provider, "measures": measures, "sub_processors": sub_processors, "locations": locations,
            "register_of_information": register, "errors": errors}


REGISTER_COLUMNS = ["rank", "provider", "lei", "country", "service", "service_type", "functions_supported",
                    "data_locations", "processing_locations", "subcontractor_of", "exit_plan"]


def annex_markdown(annex):
    provider = annex["provider"]
    lines = ["# Contract annex: security measures, sub-processors and locations", "",
             f"Generated {annex['generated_at']}{' as a generic template' if annex['generic'] else ''}."
             f" Provider: {provider['name']} (LEI {provider['lei']}, {provider['country']}).", "",
             "## Technical and organisational measures", "",
             "| Measure | Description | Verified by | GDPR |", "| --- | --- | --- | --- |"]
    lines += [f"| {cell(m['measure'])} | {cell(m['description'])} | {m['verification']} | {', '.join(m['gdpr']) or '-'} |"
              for m in annex["measures"]]
    lines += ["", "## Sub-processors", "", "Services the platform uses when they are enabled; download sources that"
              " only receive requests are listed in the external-services inventory.", "",
              "| Service | Provider | Purpose | Data received | When |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {cell(s['service'])} | {cell(s['provider'])} | {cell(s['purpose'] or '-')} | {cell(s['data'])} | {s['when']} |"
              for s in annex["sub_processors"]]
    lines += ["", "## Data and processing locations", ""]
    if annex["locations"]:
        lines += ["| Node | Roles | Region | Zone |", "| --- | --- | --- | --- |"]
        lines += [f"| {cell(n['node'])} | {cell(n['roles'])} | {cell(n['region'])} | {cell(n['zone'])} |" for n in annex["locations"]]
    else:
        lines.append(f"{TO_COMPLETE}: the servers and their locations for this installation.")
    lines += ["", "## DORA register of information", "", "| " + " | ".join(REGISTER_COLUMNS) + " |",
              "| " + " | ".join("---" for _ in REGISTER_COLUMNS) + " |"]
    lines += ["| " + " | ".join(cell(row[c]) for c in REGISTER_COLUMNS) + " |" for row in annex["register_of_information"]]
    lines += ["", *[f"Not collected: {error}" for error in annex["errors"]]]
    return "\n".join(lines).rstrip("\n") + "\n"


def isms_kit():
    sys.path.insert(0, str(ROOT / "scripts/lib"))
    import isms_kit as kit
    inventory = load_script("asset-inventory")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return kit.render(yaml.safe_load(CATALOG.read_text()), (ROOT / "docs/operations.md").read_text(),
                      inventory.external_services(inventory.EXTERNAL_SERVICES.read_text()), generated)


def write_private(directory, files):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"assurance-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    target.mkdir(mode=0o700)
    for name, content in files.items():
        with os.fdopen(os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            handle.write(content)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    survey = commands.add_parser("questionnaire")
    survey.add_argument("name", choices=sorted(p.stem for p in QUESTIONNAIRES.glob("*.yaml")))
    survey.add_argument("--live", action="store_true", help="add each control's current status from Prometheus")
    survey.add_argument("--format", choices=("markdown", "csv", "json"), default="markdown")
    annex = commands.add_parser("contract-annex")
    annex.add_argument("--generic", action="store_true", help="template without this cluster's locations")
    annex.add_argument("--format", choices=("markdown", "json"), default="markdown")
    for command in (survey, annex):
        command.add_argument("--output-dir", type=Path, help="also write the documents (mode 0600) under this directory")
    kit = commands.add_parser("isms-kit")
    kit.add_argument("--output-dir", type=Path, required=True, help="write the templates (mode 0600) under this directory")
    for field in ("name", "lei", "country"):
        annex.add_argument(f"--provider-{field}", default=TO_COMPLETE)
    annex.add_argument("--provider-exit-plan", default=EXIT_PLAN, help=f"exit plan reference (default: {EXIT_PLAN})")
    args = parser.parse_args()
    if args.command == "isms-kit":
        print(f"ISMS templates: {write_private(args.output_dir, isms_kit())}")
        return 0
    if args.command == "questionnaire":
        pack = questionnaire(args.name, args.live)
        outputs = {"markdown": questionnaire_markdown(pack), "csv": questionnaire_csv(pack), "json": json.dumps(pack, indent=2)}
        files = {f"{args.name}.md": outputs["markdown"], f"{args.name}.csv": outputs["csv"], f"{args.name}.json": outputs["json"]}
        failed = False
        if not pack["verified"]:
            print(f"Warning: the {args.name} mapping is unverified; see {QUESTIONNAIRES.name}/{args.name}.yaml.", file=sys.stderr)
    else:
        provider = {key: getattr(args, f"provider_{key}") for key in ("name", "lei", "country", "exit_plan")}
        document = contract_annex(args.generic, provider)
        outputs = {"markdown": annex_markdown(document), "json": json.dumps(document, indent=2)}
        files = {"contract-annex.md": outputs["markdown"], "contract-annex.json": outputs["json"],
                 "register-of-information.csv": to_csv(REGISTER_COLUMNS, document["register_of_information"])}
        failed = bool(document["errors"])
    print(outputs[args.format], end="" if outputs[args.format].endswith("\n") else "\n")
    if args.output_dir:
        print(f"Documents: {write_private(args.output_dir, files)}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
