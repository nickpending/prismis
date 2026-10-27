#!/usr/bin/env bash
# Builds, signs and installs Prismis.app the way an operator does, against a throwaway signing keychain
# and install directory, and checks what came out:
#   1. build-app.sh writes Prismis.app, `codesign --verify --strict` passes, the signature carries the
#      hardened runtime flag, and Info.plist sets LSUIElement and the bundle identifier;
#   2. `make install-menubar` under make's default shell, and again under dash (a POSIX shell with no
#      bash extensions), builds, signs and copies the app to MENUBAR_INSTALL_DIR;
#   3. nothing that install runs registers a login item or opens the app.
# Nothing is written outside a temp directory, and the user keychain search list is left as it was.
# macOS only. macOS bash 3.2 safe.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$APP_DIR")"
BUNDLE_ID="info.voidwire.Prismis"

[ "$(uname -s)" = Darwin ] || {
	echo "check-app: macOS only"
	exit 1
}

WORK="$(mktemp -d)"
KEYCHAIN="$WORK/signing.keychain-db"
LIST_BEFORE="$(security list-keychains -d user)"
cleanup() {
	security delete-keychain "$KEYCHAIN" 2>/dev/null || true
	rm -rf "$WORK"
}
trap cleanup EXIT
export PRISMIS_KEYCHAIN="$KEYCHAIN"
export PRISMIS_KEYCHAIN_PASSWORD_FILE="$WORK/keychain-password"

FAIL=0
fail() {
	echo "FAIL: $1"
	[ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/  | /'
	FAIL=1
}
ok() { echo "OK: $1"; }

# check_bundle LABEL APP — the signature and the Info.plist keys the app relies on.
check_bundle() {
	local label="$1" app="$2" out
	if [ ! -d "$app" ]; then
		fail "$label: no bundle at $app"
		return
	fi
	if out="$(codesign --verify --strict "$app" 2>&1)"; then
		ok "$label: codesign --verify --strict passes"
	else
		fail "$label: codesign --verify --strict failed" "$out"
	fi
	out="$(codesign -dv "$app" 2>&1)"
	# A here-string, not `printf | grep -q`: under pipefail grep's early exit breaks printf's pipe.
	if grep -q 'flags=0x[0-9a-f]*(.*runtime' <<<"$out"; then
		ok "$label: the signature carries the hardened runtime flag"
	else
		fail "$label: no hardened runtime flag" "$out"
	fi
	local plist="$app/Contents/Info.plist" ui id
	ui="$(plutil -extract LSUIElement raw -o - "$plist" 2>&1 || true)"
	id="$(plutil -extract CFBundleIdentifier raw -o - "$plist" 2>&1 || true)"
	if [ "$ui" = true ] && [ "$id" = "$BUNDLE_ID" ]; then
		ok "$label: Info.plist sets LSUIElement and CFBundleIdentifier $BUNDLE_ID"
	else
		fail "$label: Info.plist has LSUIElement=$ui CFBundleIdentifier=$id"
	fi
}

# 1. The build script on its own.
if out="$(PRISMIS_OUT_DIR="$WORK/out" "$APP_DIR/scripts/build-app.sh" 2>&1 </dev/null)"; then
	ok "build-app.sh exits 0"
else
	fail "build-app.sh failed" "$out"
fi
check_bundle "build-app.sh" "$WORK/out/Prismis.app"

# 2. The make target, under make's default shell and then under dash.
for shell in default /bin/dash; do
	dest="$WORK/Applications-${shell##*/}"
	args=(install-menubar "MENUBAR_INSTALL_DIR=$dest" "MENUBAR_BUILD_DIR=$WORK/build")
	if [ "$shell" != default ]; then
		[ -x "$shell" ] || {
			fail "$shell is not on this Mac"
			continue
		}
		args+=("SHELL=$shell")
	fi
	# A bash-only test in an `if` does not fail under dash: `[[` is a command it cannot find, so the
	# branch is skipped and the recipe goes on. dash's complaint is the evidence, so it fails the check too.
	if ! out="$(make -C "$ROOT" "${args[@]}" 2>&1 </dev/null)"; then
		fail "make install-menubar under the $shell shell failed" "$out"
	elif grep -qE ': not found|Syntax error|Bad substitution|Illegal option' <<<"$out"; then
		fail "make install-menubar under the $shell shell ran syntax that shell lacks" "$out"
	else
		ok "make install-menubar under the $shell shell exits 0 with no shell complaint"
	fi
	check_bundle "install ($shell shell)" "$dest/Prismis.app"
done

# 3. Nothing the install runs registers a login item or launches the app.
recipe="$(make -C "$ROOT" -n install-menubar "MENUBAR_INSTALL_DIR=$WORK/x" 2>&1)"
hits="$(printf '%s\n' "$recipe" | cat - "$APP_DIR/scripts/build-app.sh" |
	grep -nE 'SMAppService|launchctl|LoginItems|sfltool|osascript|(^|[;&|[:space:]])open[[:space:]]' || true)"
if [ -z "$hits" ]; then
	ok "the install recipe and build-app.sh name no login-item tool and never open the app"
else
	fail "the install path names a login-item tool or opens the app" "$hits"
fi

if [ "$(security list-keychains -d user)" = "$LIST_BEFORE" ]; then
	ok "the user keychain search list is as it was"
else
	fail "the user keychain search list changed" "$(security list-keychains -d user)"
fi

if [ "$FAIL" -ne 0 ]; then
	echo "check-app: FAIL"
	exit 1
fi
echo "check-app: PASS"
