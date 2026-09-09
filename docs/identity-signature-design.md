# Authentication and deliberate approval: unresolved production design

This is a standards-grounded design record, not an implemented identity provider, a completed signing ceremony or a Part11 certification. The current platform verifies configured-key Ed25519 signatures and uses caller-asserted operator labels. The production provider, deployment and clinical/quality owner have been requested but not supplied.

## Required boundaries

An OIDC ID token represents authentication to a relying party. It is not, by itself, evidence that the person deliberately approved an analysis plan. APIs must validate the access-token profile intended for that resource server. Stable identity is `(iss, sub)`, not email or an arbitrary operator label. See [OIDC Core §§2 and5.7](https://openid.net/specs/openid-connect-core-1_0.html) and [RFC9068 §4](https://www.rfc-editor.org/rfc/rfc9068.html#section-4).

The deployed identity profile must specify trusted issuers/metadata, audience policy, allowed algorithms, key rotation and token-type validation. Validate issuer exactly, expected audiences, expiry and transaction nonce. Current OIDC Core errata2 makes `azp` guidance extension-dependent; stricter application requirements must be labeled as application policy. Never follow an arbitrary token-supplied key URL. See [OIDC Core §3.1.3.7](https://openid.net/specs/openid-connect-core-1_0.html) and [RFC8725 §3](https://www.rfc-editor.org/rfc/rfc8725.html#section-3).

Use an authorization-code flow with S256 PKCE and transaction-specific callback/CSRF/nonce controls. Fresh approval authentication must use `auth_time`, not merely a newly issued or refreshed token's `iat`. Request `max_age` and verify the provider actually supplies the required freshness. Interpret `acr`/`amr` through the configured provider's assurance policy; an `mfa` string is not proof of identity enrollment or approval intent. See [RFC9700 §2.1](https://www.rfc-editor.org/rfc/rfc9700.html#section-2.1), [RFC9470 §§3 and6.1](https://www.rfc-editor.org/rfc/rfc9470.html#section-3), and [RFC8176 §3](https://www.rfc-editor.org/rfc/rfc8176.html#section-3).

## Approval transaction design

The application should present the frozen record and explicit approval meaning, then atomically consume a single-use server-held transaction bound to the plan digest/version, authenticated principal/session, purpose and authority-policy version. The approval evidence should include signer manifestation, execution time, meaning, authentication-event reference and enrolled signing-key binding. This is an application design inference; OIDC does not define a clinical plan-signature object or prove that the displayed content was reviewed.

Enrollment must link the verified individual to the issuer/subject, approved role and signing key through independently administered controls. Account disablement, role removal, local-session termination, token invalidation and key revocation have distinct behavior. Future approvals should be blocked appropriately while historical identity/key/authority evidence is retained. Compromise handling and historical review need a defined policy, not silent deletion of old records.

## Regulatory distinctions that must not be collapsed

- [21 CFR §11.200(a)(1)](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11) refers to two distinct identification components, not necessarily two different authentication factors. It distinguishes first/out-of-session signing from subsequent signing during continuous controlled access.
- Section11.200(a)(3) addresses non-owner misuse controls; it does not itself mandate two reviewers for every analysis. The platform's author/reviewer independence requirement is a separate project policy.
- Sections11.50 and11.70 address signer name/time/meaning and inseparable record linkage. Successful login alone does not establish those properties or the individual's intent to adopt a legally binding signature.
- FDA's [October2024 guidance, Q7–8, Q24, Q26 and Q28–29](https://www.fda.gov/media/166215/download) supports risk-based validation and technology-neutral identity/signature methods; FDA does not certify signing systems. Applicable organizational obligations and intended-use validation remain.

Before clinical admission can be enabled, evidence must cover actual provider configuration, identity/key enrollment, deliberate approval UI, account switching, stale/missing authentication, replay/concurrent callbacks, changed plans, role/key revocation, recovery and quality review. Locally generated tokens or fixture keys can test code paths but cannot prove an independently administered production identity system.
