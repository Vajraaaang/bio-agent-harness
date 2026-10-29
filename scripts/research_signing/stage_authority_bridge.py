"""Stage the minimal authority seam for review; never modify canonical source."""
from pathlib import Path
import difflib
import hashlib
import json

ROOT = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness")
REPORT = ROOT / "reports/rna-airway-implementation/2026-10-22-v1/grant-bridge"
SOURCE = ROOT / "devbench/analysis/rna_trial_authority.py"


def main():
    before = SOURCE.read_text()
    after = before.replace('"rna_trial_authority.py", "rna_scientific_trial.py",',
        '"rna_trial_authority.py", "rna_grant_signing.py", "rna_approval_setup.py", "rna_scientific_trial.py",', 1)
    signature = '    def _validated(self, request, *, purpose, binding_sha256):\n'
    assert after.count(signature) == 1
    after = after.replace(signature, '    def _validated_subject(self, request, *, purpose, binding_sha256):\n', 1)
    split = '        grant = decode_json(bounded_source(approval["evidence"], self.workspace))\n'
    assert after.count(split) == 1
    after = after.replace(split, '''        return request, approval

    def _grant_signing_scientific_preflight(self, request, *, purpose, binding_sha256):
        """Read actual scientific custody with approval honestly absent, never forged."""
        from .rna_scientific_trial import inspect_readiness
        if type(self._runtime_context) is not dict or "planner" not in self._runtime_context:
            raise PermissionError("Actual scientific planning runtime is unavailable")
        planner = self._runtime_context["planner"]
        unsigned = deepcopy(request)
        unsigned["approvals"][purpose] = None
        if trial_binding(unsigned, purpose) != binding_sha256:
            raise PermissionError("Unsigned readiness changed the approved grant subject")
        readiness = inspect_readiness(unsigned, methods=planner.methods, workspace=self.workspace,
            planner=planner, purpose=purpose, now=self.clock(), runtime_context=self._runtime_context)
        blockers = list(readiness["blockers"])
        if purpose == "trial_execution":
            from .rna_route_qualification import TrustedExecutionQualification
            from .rna_public_execution_qualification import TrustedPublicExecutionQualification
            expected_qualification = (TrustedPublicExecutionQualification if request["format"] == FORMAT_V3
                and request["dataset"]["classification"] == "public" else TrustedExecutionQualification)
            if type(self.execution_qualification) is not expected_qualification:
                raise PermissionError("Exact scientific execution qualification is unavailable")
            receipt = self.execution_qualification.verify(request, methods=planner.methods, planner=planner,
                purpose=purpose, runtime_context=self._runtime_context)
            blockers = [item for item in blockers if item not in receipt["satisfied_blockers"]]
        if (readiness["binding_sha256"] != binding_sha256
                or blockers != ["Missing separate " + purpose + " approval"]):
            raise PermissionError("Actual unsigned scientific readiness is blocked")

    @contextmanager
    def grant_signing_preflight(self, request, *, payload):
        """Hold the consumed-state lock while the trusted exact-subject signer acts.

        This does not consume a grant or grant execution authority. The same
        actual native/runtime/scientific admission checks run before and after
        the signature; unavailable qualification rejects before key access.
        """
        from .rna_grant_signing import unsigned_trial_grant_subject
        from .rna_approval_setup import _UNSIGNED_FIELDS
        if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
            raise PermissionError("A closed unsigned grant subject is required")
        purpose = payload["purpose"]
        binding = trial_binding(request, purpose)
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            request, approval = self._validated_subject(request, purpose=purpose, binding_sha256=binding)
            expected = unsigned_trial_grant_subject(request, purpose=purpose, issuer_id=payload["issuer_id"],
                grant_id=payload["grant_id"], expires_at=approval["expires_at"], now=self.clock())
            if payload != expected:
                raise PermissionError("Approved signing subject differs from current purpose approval")
            key = self.issuer_keys.get(payload["issuer_id"])
            if not isinstance(key, Ed25519PublicKey):
                raise PermissionError("Signing issuer is not an explicitly injected trusted public root")
            if db.execute("SELECT 1 FROM grants WHERE (issuer_id=? AND grant_id=?) OR (purpose=? AND binding_sha256=?)",
                    (payload["issuer_id"], payload["grant_id"], purpose, binding)).fetchone() is not None:
                raise PermissionError("Signing grant or binding already consumed; replay denied")
            self._grant_signing_scientific_preflight(request, purpose=purpose, binding_sha256=binding)
            yield key
            self._validated_subject(request, purpose=purpose, binding_sha256=binding)
            self._grant_signing_scientific_preflight(request, purpose=purpose, binding_sha256=binding)

    def _validated(self, request, *, purpose, binding_sha256):
        request, approval = self._validated_subject(request, purpose=purpose, binding_sha256=binding_sha256)
        grant = decode_json(bounded_source(approval["evidence"], self.workspace))
''', 1)
    start = after.index('        try:\n            if type(grant["signature"])')
    end = after.index('        for field, value in {"trial_id":', start)
    after = after[:start] + '''        from .rna_grant_signing import verify_authority_signature
        verify_authority_signature(signed, grant["signature"], public_key=key)
''' + after[end:]
    # Ed25519 InvalidSignature is now handled by the single shared verifier.
    after = after.replace('from cryptography.exceptions import InvalidSignature\n', '', 1)
    target = REPORT / "staged-rna_trial_authority-v2.py"
    target.write_text(after)
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile="a/devbench/analysis/rna_trial_authority.py", tofile="b/devbench/analysis/rna_trial_authority.py"))
    (REPORT / "authority-integration-v2.patch").write_text(patch)
    (REPORT / "authority-stage-v2.json").write_text(json.dumps({"canonical_source_sha256":hashlib.sha256(before.encode()).hexdigest(),
        "staged_source_sha256":hashlib.sha256(after.encode()).hexdigest(), "canonical_edited":False,
        "actual_keychain_calls":0,"actual_signatures":0,"live_grants":0},sort_keys=True,indent=2)+"\n")


if __name__ == "__main__":
    main()
