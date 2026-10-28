#!/usr/bin/env bash
# Build Prismis.app with the installed Swift toolchain (Command Line Tools is enough) and sign it with the
# stable self-signed "Prismis Signing" identity, so the app keeps one code identity across rebuilds for the
# per-app grants macOS ties to it, such as Local Network access (an ad-hoc signature changes every build).
#
# The identity lives in prismis's own keychain, created with a generated password, never the login
# keychain: that is what lets the key's partition list be set and `codesign` run with no password prompt.
# The keychain joins the user search list only while codesign runs.
#
# Building and signing never opens the app and never registers it as a login item.
#
# Environment (all optional):
#   PRISMIS_KEYCHAIN                path of the signing keychain
#                                   (default ~/Library/Keychains/prismis-signing.keychain-db)
#   PRISMIS_KEYCHAIN_PASSWORD_FILE  where its generated password lives, 0600
#                                   (default ${XDG_CONFIG_HOME:-~/.config}/prismis/signing-keychain-password)
#   PRISMIS_OUT_DIR                 where Prismis.app is written (default <repo>/dist)
# Tests point the first two at a throwaway keychain.
set -euo pipefail

IDENTITY="Prismis Signing"
BUNDLE_ID="info.voidwire.Prismis"
STEP_TIMEOUT=30

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$APP_DIR")"
KEYCHAIN="${PRISMIS_KEYCHAIN:-$HOME/Library/Keychains/prismis-signing.keychain-db}"
PASSWORD_FILE="${PRISMIS_KEYCHAIN_PASSWORD_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/prismis/signing-keychain-password}"
OUT_DIR="${PRISMIS_OUT_DIR:-$ROOT/dist}"
BUNDLE="$OUT_DIR/Prismis.app"

die() {
	echo "build-app: $*" >&2
	exit 1
}

[ "$(uname -s)" = Darwin ] || die "Prismis.app builds on macOS only"

# Every signing step is bounded: codesign blocks forever on an authorization dialog when the key's partition
# list does not admit it, instead of failing.
bounded() { perl -e 'alarm shift; exec @ARGV' "$STEP_TIMEOUT" "$@"; }

keychain_password() {
	if [ ! -s "$PASSWORD_FILE" ]; then
		mkdir -p "$(dirname "$PASSWORD_FILE")"
		(
			umask 077
			openssl rand -base64 24 | tr -d '/+=\n' >"$PASSWORD_FILE"
		)
		chmod 600 "$PASSWORD_FILE"
	fi
	cat "$PASSWORD_FILE"
}

identity_hash() {
	[ -f "$KEYCHAIN" ] || return 0
	# No -v: it filters to trusted identities and a self-signed one is untrusted, yet signs fine.
	security find-identity -p codesigning "$KEYCHAIN" 2>/dev/null |
		sed -n "s/^ *[0-9]*) \([0-9A-F]\{40\}\) \"$IDENTITY\".*/\1/p" | head -1
}

# The user keychain search list, one path per line.
search_list() { security list-keychains -d user | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//'; }

restore_search_list() {
	local paths=() line
	[ "$(search_list)" = "$1" ] && return 0
	while IFS= read -r line; do paths+=("$line"); done <<<"$1"
	security list-keychains -d user -s "${paths[@]}"
}

create_identity() {
	local tmp pass kcpass before
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	pass="prismis-transient"
	kcpass="$(keychain_password)"

	cat >"$tmp/req.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=$IDENTITY
[v3]
basicConstraints=critical,CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=critical,codeSigning
EOF
	openssl req -x509 -newkey rsa:2048 -keyout "$tmp/key.pem" -out "$tmp/crt.pem" -days 3650 -nodes \
		-config "$tmp/req.cnf" 2>/dev/null || die "openssl req failed"
	# Legacy PKCS12 parameters: openssl 3's default MAC is one macOS's importer cannot verify.
	openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/crt.pem" -out "$tmp/identity.p12" \
		-passout "pass:$pass" -name "$IDENTITY" -macalg sha1 -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES \
		2>/dev/null || die "pkcs12 export failed"

	# create-keychain may append the new keychain to the user's search list. Put the list back as it was.
	before="$(search_list)"
	if [ ! -f "$KEYCHAIN" ]; then
		mkdir -p "$(dirname "$KEYCHAIN")"
		security create-keychain -p "$kcpass" "$KEYCHAIN" || die "create-keychain failed"
	fi
	restore_search_list "$before"
	# No auto-lock: a keychain that relocks on a timer turns an unattended rebuild back into a prompt.
	security set-keychain-settings "$KEYCHAIN"
	security unlock-keychain -p "$kcpass" "$KEYCHAIN" || die "unlock-keychain failed"
	# -T puts codesign on the key's ACL so signing never prompts.
	security import "$tmp/identity.p12" -k "$KEYCHAIN" -P "$pass" -T /usr/bin/codesign -A >/dev/null ||
		die "keychain import failed"
	# The partition list is the second gate. -s selects every signing key here; this keychain holds only ours.
	security set-key-partition-list -S apple-tool:,apple: -s -k "$kcpass" "$KEYCHAIN" >/dev/null 2>&1 ||
		die "set-key-partition-list failed: codesign would prompt"
	restore_search_list "$before"
}

echo "build-app: building (release)"
(cd "$APP_DIR" && swift build -c release --product Prismis) || die "swift build failed"
BIN="$(cd "$APP_DIR" && swift build -c release --product Prismis --show-bin-path)/Prismis"
[ -x "$BIN" ] || die "no executable at $BIN"

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
cp "$BIN" "$BUNDLE/Contents/MacOS/Prismis"
cp "$APP_DIR/Resources/Info.plist" "$BUNDLE/Contents/Info.plist"
plutil -lint "$BUNDLE/Contents/Info.plist" >/dev/null || die "Info.plist is invalid"

if [ -z "$(identity_hash)" ]; then
	echo "build-app: creating the \"$IDENTITY\" identity in $KEYCHAIN"
	create_identity
fi
HASH="$(identity_hash)"
[ -n "$HASH" ] || die "no \"$IDENTITY\" identity in $KEYCHAIN"

security unlock-keychain -p "$(keychain_password)" "$KEYCHAIN" || die "unlock-keychain failed"
# codesign resolves identities through the user keychain search list, even with --keychain. Add the signing
# keychain for the length of the signing step only, and put the list back on any exit.
LIST_BEFORE="$(search_list)"
trap 'restore_search_list "$LIST_BEFORE"' EXIT
if ! grep -qxF "$KEYCHAIN" <<<"$LIST_BEFORE"; then
	paths=()
	while IFS= read -r line; do paths+=("$line"); done <<<"$LIST_BEFORE"
	security list-keychains -d user -s "${paths[@]}" "$KEYCHAIN"
fi
# Sign by hash: two identities with the same name make `--sign <name>` ambiguous.
bounded codesign --force --sign "$HASH" --keychain "$KEYCHAIN" --identifier "$BUNDLE_ID" \
	--options runtime \
	--entitlements "$APP_DIR/Resources/Prismis.entitlements" "$BUNDLE" || die "codesign failed or timed out"
codesign --verify --strict "$BUNDLE" || die "signature does not verify"
# The hardened runtime is the point of --options runtime: a bundle signed without it must not ship.
SIGNED="$(codesign -dv "$BUNDLE" 2>&1)"
grep -q 'flags=0x[0-9a-f]*(.*runtime' <<<"$SIGNED" || die "signed bundle lacks the hardened runtime flag"

echo "build-app: signed $BUNDLE"
codesign -dr - "$BUNDLE" 2>&1 | sed -n 's/^designated => /build-app: designated requirement: /p'
