# Ed25519 public key that verifies release manifests (latest.json.sig), base64 of the raw 32 bytes.
# The matching private key exists only as the GitHub Actions secret UPDATE_SIGNING_KEY (PEM) and in
# the maintainer's offline backup. Never commit it. Changing this key strands installs that have the
# old one: they will refuse every update signed with the new key.
PUBLIC_KEY = 'VnLRR56X6A/z6uMnsDzIE5MRjK5PpU2hR7cBy3g9U6g='
