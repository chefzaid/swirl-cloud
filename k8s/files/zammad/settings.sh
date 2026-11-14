#!/bin/bash
set -euo pipefail
bundle exec rails r /zammad-config/settings.rb
# Search is optional for Zammad: an unreachable Elasticsearch must not keep
# the help desk down. Build the index once it answers.
if bundle exec rails r "SearchIndexBackend.version" >/dev/null 2>&1; then
  if ! bundle exec rails r "SearchIndexBackend.index_exists?('Ticket') || exit(1)"; then
    bundle exec rake "zammad:searchindex:rebuild[2]"
  fi
else
  echo "WARNING: Elasticsearch did not answer; search stays slow until the next start." >&2
fi
