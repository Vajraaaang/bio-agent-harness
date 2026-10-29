# Local research approval signer setup

This setup prepares public-only verification material for **synthetic workflow
generation**. It creates no signer, enrolls no trusted issuer, changes no trial
request, registers no runtime resolver and starts no model, numerical run or real
study. No matching current generation grant exists. Native qualification and its
exact source/runtime bindings remain separate gates.

An owner-controlled research key establishes control of that local key. It does
not verify an institutional identity, independent scientific approval, a clinical
signature or compliance. The existing role separation, approval, purpose,
allocation, replay and runtime checks remain unchanged. Numerical execution needs
its own allocation and separate `trial_execution` approval; this helper cannot
prepare or import that approval.

## Human-only private-key operations

Only the human operates private keys in their own terminal, outside the harness.
Do not paste a private key, passphrase, account token or cached authentication into
the harness or a chat. The helper accepts a declared public 32-byte Ed25519 key in
hex; an arbitrary 32-byte value cannot reveal whether someone copied a secret
seed, so selecting the public output remains the human's responsibility.

On 2026-10-19, metadata checks found
`/opt/homebrew/opt/openssl@3/bin/openssl` reporting OpenSSL 3.6.4 and advertising
`pkeyutl -rawin`, `genpkey -algorithm` and encrypted key output. These checks did
not create a key or perform a signature. `/usr/bin/openssl` reported LibreSSL
3.3.6; its `pkeyutl` help did not advertise `-rawin`. Use the explicit verified
OpenSSL 3 path below, and recheck its version/help yourself before proceeding.
The instructions do not establish Keychain, Secure Enclave or hardware-backed
Ed25519 support, or an authenticated user-presence guarantee.

OpenSSL documents [Ed25519 key generation](https://docs.openssl.org/3.6/man1/openssl-genpkey/)
and [public key export](https://docs.openssl.org/3.6/man1/openssl-pkey/).
Its [Ed25519 signing documentation](https://docs.openssl.org/3.6/man1/openssl-pkeyutl/)
specifies signing the raw message, using `-rawin` without `-digest`.

The following commands are **for the human to run**, after choosing a new private
directory outside the repository and an encrypted-key passphrase. The example
does not put a passphrase in an argument, environment variable or file. Do not
reuse existing filenames.

```sh
RNA_OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl
RNA_SIGNER_DIR=/Users/vajraang/.local/share/rna-research-signer
RNA_SETUP=/Users/vajraang/Documents/ChatGPT/bio-agent-harness/local-research-approval
(
set -eu
umask 077
test ! -e "$RNA_SIGNER_DIR"
mkdir -p "$RNA_SIGNER_DIR"
mkdir -p "$RNA_SETUP"
test ! -e "$RNA_SETUP/issuer-public.pem"
test ! -e "$RNA_SETUP/issuer-public.der"
"$RNA_OPENSSL" genpkey -algorithm ED25519 -aes-256-cbc -out "$RNA_SIGNER_DIR/issuer-private.pem"
"$RNA_OPENSSL" pkey -in "$RNA_SIGNER_DIR/issuer-private.pem" -pubout -out "$RNA_SETUP/issuer-public.pem"
"$RNA_OPENSSL" pkey -pubin -in "$RNA_SETUP/issuer-public.pem" -text -noout
"$RNA_OPENSSL" pkey -pubin -in "$RNA_SETUP/issuer-public.pem" -outform DER -out "$RNA_SETUP/issuer-public.der"
"$RNA_OPENSSL" dgst -sha256 "$RNA_SETUP/issuer-public.der"
)
```

If the signer directory already exists, stop and choose a new directory; the
first line checking it must succeed before key generation. The `pub:` section of
the public-only text output contains the 32 public bytes. Copy those bytes as 64
hex characters, removing colons and whitespace. Independently retain the SHA-256
of the public SubjectPublicKeyInfo DER and the chosen issuer ID through a channel
you control. That accepted fingerprint must come from this independent check,
not from accepting a fingerprint declared by the proposal itself.

## Public-only proposal and grant preparation

Use the independently verified ordinary-wheel Python from the S6 artifact report
as `RNA_PYTHON`; the expected final path is
`/Users/vajraang/Documents/ChatGPT/bio-agent-harness/r/s6/bin/python`, pending that
report's build and verification. Do not run the commands until the report
confirms it. Do not substitute a checkout or editable install. The examples use
`-I -B` for isolated installed imports without writing bytecode. The module and
script offer only `--propose-public-issuer`, `--prepare-generation`, and
`--import-generation-signature`. All input/output file paths must be absolute,
canonical, ordinary files inside `--workspace`; outputs must be new files.
Inputs are bounded to 1 MB, public proposals to 16 KB and signatures to 64 bytes.
No environment variable supplies trust or a backend.

Use the approved local label `bio-harness-local-research-vajraang` as the issuer
ID; this label makes no authenticated identity claim. Replace only the public hex
placeholder with the public bytes from the external tool. The proposed issuer remains `enabled: false` and has
only the fixed `synthetic` / `workflow_generation` scope.

```sh
RNA_PYTHON=/Users/vajraang/Documents/ChatGPT/bio-agent-harness/r/s6/bin/python
RNA_PUBLIC_HEX=REPLACE_WITH_64_PUBLIC_HEX_CHARACTERS
RNA_ISSUER_ID=bio-harness-local-research-vajraang
"$RNA_PYTHON" -I -B -m scripts.rna_approval_setup --propose-public-issuer \
  --workspace "$RNA_SETUP" --issuer-id "$RNA_ISSUER_ID" \
  --public-key-hex "$RNA_PUBLIC_HEX" --output "$RNA_SETUP/issuer-proposal.json"
```

The loader requires both the separately accepted fingerprint and expected issuer
ID. It returns a public verification key only. It never enables a proposal or
adds that key to `AuthenticatedTrialAuthority`.

For a retained, schema-valid **synthetic** request, first review the actual
question, requirements, biological-unit declarations, operator and role map,
generation allocation and engineering evidence. This helper does not inspect an
expression matrix, confirm scientific validity, manufacture qualification or
derive requirements from defaults. Use a new grant ID and an explicit future
timezone-aware expiry. Preparation writes the exact existing unsigned grant JSON
with `trial_binding(request, "workflow_generation")` and
`trial_purpose_allocation(request, "workflow_generation")`; a version 3 generation
subject has `numeric: null`. The request remains unchanged.

```sh
"$RNA_PYTHON" -I -B -m scripts.rna_approval_setup --prepare-generation \
  --workspace "$RNA_SETUP" --request "$RNA_SETUP/synthetic-request.json" \
  --issuer-id "$RNA_ISSUER_ID" --grant-id REPLACE_WITH_NEW_GRANT_ID \
  --expires-at REPLACE_WITH_FUTURE_ISO_TIME_WITH_Z_OR_OFFSET \
  --output "$RNA_SETUP/unsigned-generation.json"
```

Read the unsigned JSON before signing. It grants only the exact subject encoded
there; its `engineering_sha256` is a binding to supplied engineering declarations,
not a qualification certificate. Sign its **exact bytes** without editing,
pretty-printing, adding a newline or hashing the message first. The existing
`devharness.canonical-json/1` encoding is the signature input, not RFC 8785 JCS.

The human alone performs the external signing operation:

```sh
(
set -eu
test ! -e "$RNA_SETUP/generation.sig"
"$RNA_OPENSSL" pkeyutl -sign -rawin \
  -inkey "$RNA_SIGNER_DIR/issuer-private.pem" \
  -in "$RNA_SETUP/unsigned-generation.json" -out "$RNA_SETUP/generation.sig"
"$RNA_OPENSSL" pkeyutl -verify -rawin -pubin \
  -inkey "$RNA_SETUP/issuer-public.pem" \
  -in "$RNA_SETUP/unsigned-generation.json" -sigfile "$RNA_SETUP/generation.sig"
)
```

Use the independently retained fingerprint below, after deliberately accepting
that local research issuer ID. Do not derive this argument from the proposal.

```sh
RNA_ACCEPTED_FINGERPRINT=REPLACE_WITH_INDEPENDENTLY_ACCEPTED_SPKI_SHA256
"$RNA_PYTHON" -I -B -m scripts.rna_approval_setup --import-generation-signature \
  --workspace "$RNA_SETUP" --request "$RNA_SETUP/synthetic-request.json" \
  --proposal "$RNA_SETUP/issuer-proposal.json" \
  --unsigned-payload "$RNA_SETUP/unsigned-generation.json" \
  --signature "$RNA_SETUP/generation.sig" --issuer-id "$RNA_ISSUER_ID" \
  --accepted-fingerprint "$RNA_ACCEPTED_FINGERPRINT" \
  --output "$RNA_SETUP/verified-generation-grant.json"
```

Import independently verifies the public root, the signature over canonical bytes,
the entire expected generation subject, synthetic scope and expiry. It returns
the existing closed `devbench.rna-trial-authority-grant/1` record with a detached
signature encoded as 128 hex characters. It does not install an approval, consume
a grant, create a replay ledger, qualify a native runtime or dispatch anything.
The trusted coordinator still needs deliberate issuer injection, a matching
approval evidence reference, qualified exact runtime and fresh admission checks.
Public and private real-study requests remain rejected by this setup helper.
