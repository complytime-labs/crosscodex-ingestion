| #      | Description                                                                                                                                                                                                                                                             |   Level |
|--------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.3.4 | Verify that, if the resource server requires specific authentication strength, methods, or recentness, it verifies that the presented access token satisfies these constraints. For example, if present, using the OIDC 'acr', 'amr'and 'auth_time'claims respectively. |       2 |
| 10.3.5 | Verify that the resource server prevents the use of stolen access tokens or replay of access tokens (from unauthorized parties) by requiring sender-constrained access tokens, either Mutual TLS for OAuth 2 or OAuth 2 Demonstration of Proof of Possession (DPoP).    |       3 |

## V10.4 OAuth Authorization Server

These requirements detail the responsibilities for OAuth authorization servers, including OpenID Providers.

For client authentication, the 'self\_signed\_tls\_client\_auth'method is allowed with the prerequisites required by section 2.2 of RFC 8705.

| #      | Description                                                                                                                                                                                                                                                                                                                                                                            |   Level |
|--------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.4.1 | Verify that the authorization server validates redirect URIs based on a client-specific allowlist of pre-registered URIs using exact string comparison.                                                                                                                                                                                                                                |       1 |
| 10.4.2 | Verify that, if the authorization server returns the authorization code in the authorization response, it can be used only once for a token request. For the second valid request with an authorization code that has already been used to issue an access token, the authorization server must reject a token request and revoke any issued tokens related to the authorization code. |       1 |
| 10.4.3 | Verify that the authorization code is short-lived. Themaximum lifetime can be up to 10 minutes for L1 and L2 applications and up to 1 minute for L3 applications.                                                                                                                                                                                                                      |       1 |
| 10.4.4 | Verify that for a given client, the authorization server only allows the usage of grants that this client needs to use. Note that the grants 'token'(Implicit flow) and 'password'(Resource Owner Password Credentials flow) must no longer be used.                                                                                                                                   |       1 |

| #       | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |   Level |
|---------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.4.5  | Verify that the authorization server mitigates refresh token replay attacks for public clients, preferably using sender-constrained refresh tokens, i.e., Demonstrating Proof of Possession (DPoP) or Certificate-Bound Access Tokens using mutual TLS (mTLS). For L1 and L2 applications, refresh token rotation may be used. If refresh token rotation is used, the authorization server must invalidate the refresh token after usage, and revoke all refresh tokens for that authorization if an already used and invalidated refresh token is provided. |       1 |
| 10.4.6  | Verify that, if the code grant is used, the authorization server mitigates authorization code interception attacks by requiring proof key for code exchange (PKCE). For authorization requests, the authorization server must require a valid 'code_challenge'value and must not accept a 'code_challenge_method'value of 'plain'. For a token request, it must require validation of the 'code_verifier'parameter.                                                                                                                                          |       2 |
| 10.4.7  | Verify that if the authorization server supports unauthenticated dynamic client registration, it mitigates the risk of malicious client applications. It must validate client metadata such as any registered URIs, ensure the user's consent, and warn the user before processing an authorization request with an untrusted client application.                                                                                                                                                                                                            |       2 |
| 10.4.8  | Verify that refresh tokens have an absolute expiration, including if sliding refresh token expiration is applied.                                                                                                                                                                                                                                                                                                                                                                                                                                            |       2 |
| 10.4.9  | Verify that refresh tokens and reference access tokens can be revoked by an authorized user using the authorization server user interface, to mitigate the risk of malicious clients or stolen tokens.                                                                                                                                                                                                                                                                                                                                                       |       2 |
| 10.4.10 | Verify that confidential client is authenticated for client-to-authorized server backchannel requests such as token requests, pushed authorization requests (PAR), and token revocation requests.                                                                                                                                                                                                                                                                                                                                                            |       2 |
| 10.4.11 | Verify that the authorization server configuration only assigns the required scopes to the OAuth client.                                                                                                                                                                                                                                                                                                                                                                                                                                                     |       2 |
| 10.4.12 | Verify that for a given client, the authorization server only allows the 'response_mode'value that this client needs to use. For example, by having the authorization server validate this value against the expected values or by using pushed authorization request (PAR) or JWT-secured Authorization Request (JAR).                                                                                                                                                                                                                                      |       3 |
| 10.4.13 | Verify that grant type 'code'is always used together with pushed authorization requests (PAR).                                                                                                                                                                                                                                                                                                                                                                                                                                                               |       3 |

| #       | Description                                                                                                                                                                                                                                                                                                                                                          |   Level |
|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.4.14 | Verify that the authorization server issues only sender-constrained (Proof-of-Possession) access tokens, either with certificate-bound access tokens using mutual TLS (mTLS) or DPoP-bound access tokens (Demonstration of Proof of Possession).                                                                                                                     |       3 |
| 10.4.15 | Verify that, for a server-side client (which is not executed on the end-user device), the authorization server ensures that the 'authorization_details' parameter value is from the client backend and that the user has not tampered with it. For example, by requiring the usage of pushed authorization request (PAR) or JWT-secured Authorization Request (JAR). |       3 |
| 10.4.16 | Verify that the client is confidential and the authorization server requires the use of strong client authentication methods (based on public-key cryptography and resistant to replay attacks), such as mutual TLS ( 'tls_client_auth', 'self_signed_tls_client_auth') or private key JWT ( 'private_key_jwt').                                                     |       3 |

## V10.5 OIDC Client

As the OIDC relying party acts as an OAuth client, the requirements from the section "OAuth Client" apply as well.

Note that the "Authentication with an Identity Provider"section in the "Authentication"chapter also contains relevant general requirements.

| #      | Description                                                                                                                                                                                                                                                                                                                                            |   Level |
|--------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.5.1 | Verify that the client (as the relying party) mitigates ID Token replay attacks. For example, by ensuring that the 'nonce'claim in the ID Token matches the 'nonce'value sent in the authentication request to the OpenID Provider (in OAuth2 refereed to as the authorization request sent to the authorization server).                              |       2 |
| 10.5.2 | Verify that the client uniquely identifies the user from ID Token claims, usually the 'sub'claim, which cannot be reassigned to other users (for the scope of an identity provider).                                                                                                                                                                   |       2 |
| 10.5.3 | Verify that the client rejects attempts by amalicious authorization server to impersonate another authorization server through authorization server metadata. The client must reject authorization server metadata if the issuer URL in the authorization server metadata does not exactly match the pre-configured issuer URL expected by the client. |       2 |

| #      | Description                                                                                                                                                                                                                                                                                                                                                                                                                                 |   Level |
|--------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.5.4 | Verify that the client validates that the ID Token is intended to be used for that client (audience) by checking that the 'aud'claim from the token is equal to the 'client_id'value for the client.                                                                                                                                                                                                                                        |       2 |
| 10.5.5 | Verify that, when using OIDC back-channel logout, the relying party mitigates denial of service through forced logout and cross-JWT confusion in the logout flow. The client must verify that the logout token is correctly typed with a value of 'logout+jwt', contains the 'event'claim with the correct member name, and does not contain a 'nonce'claim. Note that it is also recommended to have a short expiration (e.g., 2 minutes). |       2 |

## V10.6 OpenID Provider

As OpenID Providers act as OAuth authorization servers, the requirements from the section "OAuth Authorization Server"apply as well.

Note that if using the ID Token flow (not the code flow), no access tokens are issued, and many of the requirements for OAuth AS are not applicable.

| #      | Description                                                                                                                                                                                                                                                                     |   Level |
|--------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.6.1 | Verify that the OpenID Provider only allows values 'code', 'ciba', 'id_token', or 'id_token code'for response mode. Note that 'code'is preferred over 'id_token code'(the OIDC Hybrid flow), and 'token'(any Implicit flow) must                                                |       2 |
| 10.6.2 | not be used. Verify that the OpenID Provider mitigates denial of service through forced logout. By obtaining explicit confirmation from the end-user or, if present, validating parameters in the logout request (initiated by the relying party), such as the 'id_token_hint'. |       2 |

## V10.7 Consent Management

These requirements cover the verification of the user's consent by the authorization server. Without proper user consent verification, a malicious actor may obtain permissions on the user's behalf through spoofing or social-engineering.

| #      | Description                                                                                                                                                                                                                                                                                                                                                                                                                    |   Level |
|--------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------|
| 10.7.1 | Verify that the authorization server ensures that the user consents to each authorization request. If the identity of the client cannot be assured, the authorization server must always explicitly prompt the user for consent.                                                                                                                                                                                               |       2 |
| 10.7.2 | Verify that when the authorization server prompts for user consent, it presents sufficient and clear information about what is being consented to. When applicable, this should include the nature of the requested authorizations (typically based on scope, resource server, Rich Authorization Requests (RAR) authorization details), the identity of the authorized application, and the lifetime of these authorizations. |       2 |
| 10.7.3 | Verify that the user can review, modify, and revoke consents which the user has granted through the authorization server.                                                                                                                                                                                                                                                                                                      |       2 |

## References

For more information on OAuth, please see:

- [oauth.net](https://oauth.net/)
- [OWASP OAuth 2.0 Protocol Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/OAuth2_Cheat_Sheet.html)

For OAuth-related requirements in ASVS following published and in draft status RFC-s are used:

- [RFC6749 The OAuth 2.0 Authorization Framework](https://datatracker.ietf.org/doc/html/rfc6749)
- [RFC6750 The OAuth 2.0 Authorization Framework: Bearer TokenUsage](https://datatracker.ietf.org/doc/html/rfc6750)
- [RFC6819 OAuth 2.0 Threat Model and Security Considerations](https://datatracker.ietf.org/doc/html/rfc6819)
- [RFC7636 Proof Key for Code Exchange by OAuth Public Clients](https://datatracker.ietf.org/doc/html/rfc7636)
- [RFC7591 OAuth 2.0 Dynamic Client Registration Protocol](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC8628 OAuth 2.0 Device Authorization Grant](https://datatracker.ietf.org/doc/html/rfc8628)
- [RFC8707 Resource Indicators for OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc8707)
- [RFC9068 JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens](https://datatracker.ietf.org/doc/html/rfc9068)
- [RFC9126 OAuth 2.0 Pushed Authorization Requests](https://datatracker.ietf.org/doc/html/rfc9126)
- [RFC9207 OAuth 2.0 Authorization Server Issuer Identification](https://datatracker.ietf.org/doc/html/rfc9207)
- [RFC9396 OAuth 2.0 Rich Authorization Requests](https://datatracker.ietf.org/doc/html/rfc9396)
- [RFC9449 OAuth 2.0 Demonstrating Proof of Possession (DPoP)](https://datatracker.ietf.org/doc/html/rfc9449)
- [RFC9700 Best Current Practice for OAuth 2.0 Security](https://datatracker.ietf.org/doc/html/rfc9700)
- [draftOAuth 2.0 for Browser-Based Applications](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-browser-based-apps)
- [draft The OAuth 2.1 Authorization Framework](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-12)

For more information on OpenID Connect, please see:

- [OpenIDConnect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)

- Hash function with less than 254 bit of output have insufficient collision resistancea and must not be used for digital signature or other applications requiring collision resistance. For other usages, they might be used for compatibility and verification ONLY with legacy systems but must not be used in new designs.

| Hash function   | Reference                            | StatusRestrictions                                              |
|-----------------|--------------------------------------|-----------------------------------------------------------------|
| SHA3-512        | FIPS 202                             | A                                                               |
| SHA-512         | FIPS 180-4                           | A                                                               |
| SHA3-384        | FIPS 202                             | A                                                               |
| SHA-384         | FIPS 180-4                           | A                                                               |
| SHA3-256        | FIPS 202                             | A                                                               |
| SHA-512/256     | FIPS 180-4                           | A                                                               |
| SHA-256         | FIPS 180-4                           | A                                                               |
| SHAKE256        | FIPS 202                             | A                                                               |
| BLAKE2s         | BLAKE2: simpler, smaller, fast asMD5 | A                                                               |
| BLAKE2b         | BLAKE2: simpler, smaller, fast asMD5 | A                                                               |
| BLAKE3          | BLAKE3 one function, fast everywhere | A                                                               |
| SHA-224         | FIPS 180-4                           | L Not suit- able for HMAC, KDF, RBG, dig- i- tal sig- na- tures |

| Hash function   | Reference   | StatusRestrictions   | StatusRestrictions                                            |
|-----------------|-------------|----------------------|---------------------------------------------------------------|
| SHA-512/224     | FIPS 180-4  | L                    | Not suit- able for HMAC, KDF, RBG, dig- i- tal sig- na- tures |
| SHA3-224        | FIPS 202    | L                    | Not suit- able for HMAC, KDF, RBG, dig- i- tal sig- na- tures |

| Hash function    | Reference           | StatusRestrictions                                              |
|------------------|---------------------|-----------------------------------------------------------------|
| SHA-1            | RFC 3174 & RFC 6194 | L Not suit- able for HMAC, KDF, RBG, dig- i- tal sig- na- tures |
| CRC (any length) |                     | D                                                               |
| MD4              | RFC 1320            | D                                                               |
| MD5              | RFC 1321            | D                                                               |

## Hash Functions for Password Storage

For secure password hashing, dedicated hash functions must be used. These slow-hashing algorithms mitigate brute-force and dictionary attacks by increasing the computational difficulty of password cracking.

| KDF                    | Reference                          | Required Parameters                                                                              | Status   |
|------------------------|------------------------------------|--------------------------------------------------------------------------------------------------|----------|
| argon2id               | RFC 9106                           | t = 1: m ≥ 47104 (46 MiB), p = 1t = 2: m ≥ 19456 (19 MiB), p = 1t ≥ 3: m ≥ 12288 (12 MiB), p = 1 | A        |
| scrypt                 | RFC 7914                           | p = 1: N ≥ 2^17 (128 MiB), r = 8p = 2: N ≥ 2^16 (64 MiB), r = 8p ≥ 3: N ≥ 2^15 (32 MiB), r = 8   | A        |
| bcrypt                 | A Future-Adaptable Password Scheme | cost ≥ 10                                                                                        | A        |
| PBKDF2- HMAC- SHA- 512 | NIST SP 800-132, FIPS 180-4        | iterations ≥ 210,000                                                                             | A        |

- Verify that mechanisms are in place to detect and respond to configuration drift in deployed environments. This may include using immutable infrastructure, automated redeployment from a secure baseline, or drift detection tools that compare current state against approved configurations.
- Verify that configuration hardening is performed on all third-party products, libraries, frameworks, and services as per their individual recommendations.

## References:

- [OWASP Threat Modeling Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Threat_Modeling_Cheat_Sheet.html)
- [OWASP Threat modeling](https://owasp.org/www-community/Application_Threat_Modeling)
- [OWASP Software Assurance Maturity Model Project](https://owasp.org/www-project-samm/)
- [Microsoft SDL](https://www.microsoft.com/en-us/securityengineering/sdl/)

## Appendix E - Contributors

We gratefully acknowledge the contributions of the following people who have commented or opened pull requests since the the release of ASVS 4.0.0.

If you are aware of any mistakes or would like your name to appear differently, please let us know.

| Johan Sydseter (sydseter)    | luis servin (lfservin)            | Oleksii Dovydkov (oleksiidov)               | IZUKA Masahiro (maizuka)        |
|------------------------------|-----------------------------------|---------------------------------------------|---------------------------------|
| James Sulinski (jsulinski)   | Eli Saad (ThunderSon)             | kkshitish9                                  | Andrew van der Stock (vanderaj) |
| Rick M (kingthorin)          | Bankde Eakasit (Bankde)           | Michael Gargiullo (mgargiullo)              | Raphael Dunant (Racater)        |
| Cesar Kohl (cesarkohl)       | inaz0                             | Joerg Bruenner (JoergBruenner)              | David Deatherage (securitydave) |
| John Carroll (yosignals)     | JimFenton (jimfenton)             | Matteo Pace (M4tteoP)                       | Sebastien gioria (SPoint42)     |
| Steven van der Baan (vdbaan) | Jeremy Bonghwan Choi (jeremychoi) | craig-shony                                 | Riccardo Sirigu (ricsirigu)     |
| Tomasz Wrobel (tw2as)        | Alena Dubeshko (belalena)         | Rafael Green (RafaelGreen1)                 | mjang-cobalt                    |
| clallier94                   | KevinW. Wall (kwwall)             | Jordan Sherman (jsherm-fwdsec / deleterepo) | Ingo Rauner (ingo-rauner)       |

| DirkWetter (drwetter)            | Moshe Zioni (moshe-apiiro)   | Patrick Dwyer (coderpatros)       | David Clarke (davidclarke-au)      |
|----------------------------------|------------------------------|-----------------------------------|------------------------------------|
| Takaharu Ogasa (takaharuogasa)   | Arkadii Yakovets (arkid15r)  | Motoyasu Saburi (motoyasu-saburi) | leirn                              |
| wet-certitude                    | timhemel                     | RL Thornton (thornshadow99)       | Thomas Bandt (aspnetde)            |
| Roel Storms (roelstorms)         | Jeroen Willemsen (commjoen)  | anonymous-31                      | Kamran Saifullah (deFr0ggy)        |
| Steve Springett (stevespringett) | Spyros (northdpole)          | Hans Herrera (hansphp)            | Marx314                            |
| CarlosAllendes                   | Yonah Russ (yruss972)        | Sander Maijers (sanmai-NL)        | Luboš Bretschneider (bretik)       |
| Eva Sarafianou (esarafianou)     | ataseren                     | Steve Thomas (Sc00bz)             | Dominique RIGHETTO (righettod)     |
| Steven van der Baan (svdb-ncc)   | Michael Vacarella (Aif4thah) | Tonimir Kisasondi (tkisason)      | Stefan Streichsbier (streichsbaer) |
| hi-unc1e                         | sb3k (starbuck3000)          | mario-platt                       | Devdatta Akhawe (devd)             |
| Michael Gissing (scolytus)       | Jet Anderson (thatsjet)      | DaveWichers (davewichers)         | Jonny Schnittger (JonnySchnittger) |
| Silvia Väli (silviavali)         | jackgates73                  | 1songb1rd                         | Timur - (timurozkul)               |
| Gareth Heyes (hackvertor)        | appills                      | suvikaartinen                     | chaals (chaals)                    |
| DanielPharos (AtlasHackert)      | will Farrell (willfarrell)   | Alina Vasiljeva (avasiljeva)      | Paul McCann (ismisepaul)           |
| Sage (SajjadPourali)             | rbsec                        | Benedikt Bauer (mastacheata)      | James Jardine (jamesjardine)       |
| Mark Burnett (m8urnett)          | dschwarz91                   | Cyber-AppSec (Cyber-AppSec)       | Tib3rius                           |
| BitnessWise (bitnesswise)        | damienbod (damienbod)        | Jared Meit (jmeit-fwdsec)         | Stefan Seelmann (sseelmann)        |
| Brendan O'Connor (ussjoin)       | Andrei Titov (andrettv)      | Hans-Petter Fjeld (atluxity)      | markehack                          |
| Neil Madden (NeilMadden)         | Michael Geramb (mgeramb)     | Osama Elnaggar (ossie-git)        | mackowski                          |