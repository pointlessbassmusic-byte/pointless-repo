# Sourced by keys.sh and apply_keys.sh: the maker test's operator config.
# The live settings go in config/maker_test.local.yaml (git-ignored), never in
# the tracked config/maker_test.yaml, so the daily `git pull --ff-only` in
# update.sh can never stall on a hand edit. An earlier keys.sh edited the
# tracked file in place: those edits are carried into the local copy once and
# the tracked file is reset.
MAKER_DIR=${MAKER_DIR:-/opt/sportsbot}   # overridable only for the test
MAKER_LOCAL=$MAKER_DIR/config/maker_test.local.yaml

maker_local_config() {   # $1 = dollars actually deposited
  local B=$MAKER_DIR DEP=${1:-100} C=$MAKER_LOCAL A=$MAKER_DIR/config/allocation.local.yaml
  if [ ! -f "$C" ]; then
    cp "$B/config/maker_test.yaml" "$C"
    (cd "$B" && git checkout -- config/maker_test.yaml 2>/dev/null) || true
  fi
  sed -i 's/^mode: paper /mode: live  /' "$C"
  sed -i "/^  real:/{n;s/starting_balance: [0-9.]*/starting_balance: $DEP/}" "$C"
  if awk -v d="$DEP" 'BEGIN{exit !(d<100)}'; then sed -i "s/^  amount: [0-9.]* /  amount: $DEP /" "$C"; fi
  if [ ! -f "$A" ]; then
    cp "$B/config/allocation.yaml" "$A"
    sed -i 's/^  budget_usd: 0.0 /  budget_usd: 30.0 /; s/^  weekly_loss_stop_usd: 0.0 /  weekly_loss_stop_usd: 15.0 /' "$A"
    sed -i -E 's#^(  (baseball|hockey|football)/touch/maker: +\{weight: null, learn: )false#\1true#' "$A"
  fi
  chown sportsbot:sportsbot "$C" "$A" "$B/config/maker_test.yaml" 2>/dev/null || true
}
