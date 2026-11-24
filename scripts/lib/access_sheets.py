"""One access review sheet per Keycloak access group (TODO §47, access reviews by
the people who know): the group's members, how and since when they hold it, and
Keep or Revoke columns for the group's owner (config/access-owners.yaml)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OWNERS = ROOT / "config/access-owners.yaml"


def owners(path=OWNERS):
    return (yaml.safe_load(path.read_text()) or {}).get("owners") or {}


def reviewed_group(name):
    return name == "platform-admins" or name.startswith("access-")


def sheets(review, grants, realm, owner_map=None):
    """{group: Markdown sheet} for every access group with members in the platform realm."""
    owner_map = owners() if owner_map is None else owner_map
    active = {}
    for grant in grants:
        group = grant.get("group") or "platform-admins"
        if (grant.get("kind") or "admin") != "guest":
            active[(grant["user"], group)] = grant
    members = {}
    for identity in review["identities"]:
        if identity["system"] != "Keycloak" or identity["scope"] != realm:
            continue
        for path in identity["groups"]:
            group = path.strip("/")
            if reviewed_group(group):
                members.setdefault(group, []).append(identity)
    result = {}
    for group, people in sorted(members.items()):
        owner = owner_map.get(group) or "the platform administrators"
        lines = [f"# Access review: {group}", "", f"Generated {review['generated_at']} UTC for {owner}.",
                 "Mark each line Keep or Revoke. Revoke time-limited grants with `./install.sh --admin-access revoke`,"
                 " permanent memberships in Keycloak, then sign below.", "",
                 "| Person | Email | Status | Held | Approved by | Ends | Last activity | Keep / Revoke |",
                 "|---|---|---|---|---|---|---|---|"]
        for person in sorted(people, key=lambda p: p["identity"]):
            grant = active.get((person["identity"], group))
            held = f"grant since {grant['granted_at'][:10]}" if grant else "permanent member"
            lines.append(f"| {person['identity']} | {person['email']} | {'active' if person['active'] else 'disabled'} | {held} | "
                         f"{grant.get('approved_by', '') if grant else ''} | {grant['expires_at'][:10] if grant else ''} | "
                         f"{person['last_activity'] or 'not recorded'} | |")
        lines += ["", f"Reviewer ({owner}): ____________________  Date: ____________  Signature: ____________________", ""]
        result[group] = "\n".join(lines)
    return result


# Runs inside the Zammad pod (corp/zammad.yaml); the tickets' content arrives on
# standard input, so no name or address appears in a command line.
ZAMMAD_TICKETS = r"""
require "json"
UserInfo.current_user_id = 1
JSON.parse(STDIN.read).each do |item|
  owner = User.find_by(email: item["owner"].downcase)
  if owner.nil?
    puts "SWIRL_SKIPPED #{item["group"]} no Zammad account for the owner"
    next
  end
  ticket = Ticket.create!(title: item["title"], group: Group.find_by(name: "Users") || Group.first, customer_id: owner.id,
                          state: Ticket::State.find_by(name: "open") || Ticket::State.first,
                          priority: Ticket::Priority.find_by(name: "2 normal") || Ticket::Priority.first)
  Ticket::Article.create!(ticket_id: ticket.id, subject: item["title"], body: item["body"], content_type: "text/plain",
                          internal: false, sender: Ticket::Article::Sender.find_by(name: "Agent"),
                          type: Ticket::Article::Type.find_by(name: "note"))
  puts "SWIRL_TICKET #{item["group"]} #{ticket.number}"
end
"""


def ticket_requests(group_sheets, owner_map, stamp):
    """[(ticket request)] for groups whose owner is an email address; the others are listed as skipped."""
    requests, skipped = [], []
    for group, sheet in sorted(group_sheets.items()):
        owner = (owner_map.get(group) or "").strip()
        if "@" not in owner:
            skipped.append(f"{group}: no owner email in config/access-owners.yaml")
            continue
        requests.append({"group": group, "owner": owner, "title": f"Access review {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}: {group}", "body": sheet})
    return requests, skipped
