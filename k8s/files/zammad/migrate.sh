#!/bin/bash
set -euo pipefail
if ! (bundle exec rails r 'puts User.any?' 2>/dev/null | grep -q true); then
  bundle exec rake db:migrate db:seed
else
  bundle exec rake db:migrate
  bundle exec rails r "Locale.sync; Translation.sync"
fi
