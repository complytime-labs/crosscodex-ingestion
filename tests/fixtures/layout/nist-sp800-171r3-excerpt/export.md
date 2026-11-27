## 2. The Fundamentals

This section describes the assumptions and methodology used to develop the requirements to protect the confidentiality of CUI in nonfederal systems and organizations. It also includes the tailoring 10 criteria applied to the controls in SP 800-53 [8].

## 2.1. Security Requirement Assumptions

The security requirements in this publication are based on the following assumptions:

- Federal information designated as CUI has the same value, whether such information resides in a federal or nonfederal system or organization.
- Statutory and regulatory requirements for the protection of CUI are consistent in federal and nonfederal systems and organizations.
- Safeguards implemented to protect CUI are consistent in federal and nonfederal systems and organizations.
- The confidentiality impact value for CUI is no less than moderate. 11
- Nonfederal organizations can directly implement a variety of potential security solutions or use external service providers to satisfy security requirements.

## 2.2. Security Requirement Development Methodology

Starting with the SP 800-53 controls in the SP 800-53B [12] moderate baseline, the controls are tailored to eliminate selected controls or parts of controls that are:

- Primarily the responsibility of the Federal Government,
- Not directly related to protecting the confidentiality of CUI,
- Adequately addressed by other related controls, 12 or
- Not applicable.

SP 800-171 security requirements represent a subset of the controls that are necessary to protect the confidentiality of CUI. The security requirements are organized into 17 families, as illustrated in Table 1. Each family contains the requirements related to the general security topic of the family. Certain families from SP 800-53 are not included due to the tailoring criteria. For example, the PII Processing and Transparency (PT) family is not included because personally identifiable information (PII) is a category of CUI, and therefore, no additional requirements are specified for confidentiality protection. The Program Management (PM) family is not included because it is not associated with any control baseline. Finally, the Contingency Planning (CP) family is not included because it addresses availability. 13

10 Tailoring is the process by which control baselines are modified to achieve certain organizational goals and objectives [13].

11 In accordance with 32 CFR 2002 [5], CUI is categorized at no less than the FIPS 199 [6] moderate confidentiality impact value. However, when federal law, regulation, or government-wide policy establishing the control of CUI specifies controls that differ from those of the moderate control baseline, then the applicable law, regulation, or government-wide policy is followed.

12 "Adequately addressed by other related controls" means that the protection capability offered by the control is provided by another control in the same or different control family. Using this tailoring option helps to eliminate potential redundancy in requirements without affecting the protection of CUI in nonfederal systems and organizations.

Table 1. Security Requirement Families

| Access Control                    | Maintenance         | Security Assessment and Monitoring   |
|-----------------------------------|---------------------|--------------------------------------|
| Awareness and Training            | Media Protection    | System and Communications Protection |
| Audit and Accountability          | Personnel Security  | System and Information Integrity     |
| Configuration Management          | Physical Protection | Planning                             |
| Identification and Authentication | Risk Assessment     | System and Services Acquisition      |
| Incident Response                 |                     | Supply Chain Risk Management         |

Organization-defined parameters (ODPs) are included in certain security requirements. ODPs provide flexibility through the use of assignment and selection operations to allow federal agencies and nonfederal organizations to specify values for the designated parameters in the requirements. 14 Assignment and selection operations provide the capability to customize the security requirements based on specific protection needs. The determination of ODP values can be guided and informed by laws, Executive Orders, directives, regulations, policies, standards, guidance, or mission and business needs. Once specified, the values for the organizationdefined parameters become part of the requirement.

## ORGANIZATION-DEFINED PARAMETERS

Organization-defined parameters are an important part of a security requirement specification. ODPs provide both the flexibility and specificity needed by organizations to clearly define their CUI security requirements, given the diverse nature of their missions, business functions, operational environments, and risk tolerance. In addition, ODPs support consistent security assessments in determining whether specified security requirements have been satisfied. If a federal agency or a consortium of agencies do not specify a particular value or range of values for an ODP, nonfederal organizations must assign the value or values to complete the security requirement.

A discussion section is included with each requirement. It is derived from the control discussion sections in SP 800-53 and provides additional information to facilitate the implementation and assessment of the requirements. The discussion section is informative, not normative. It is not intended to extend the scope of a requirement or influence the solutions that organizations may use to satisfy a requirement. The use of examples is notional, not exhaustive, and does not reflect the potential options available to organizations. A references section provides the source controls 15 from SP 800-53 and a list of NIST Special Publications with additional information on the topic described in the security requirement. The structure and content of a typical security requirement is provided in the example below.

13 CP-09 and CP-09(08) are included by exception to ensure the confidentiality of backup information is projected.

14 NIST does not establish or assign values for ODPs. If ODP values for selected security requirements are not formally established or assigned by a federal agency or a consortium of federal agencies, nonfederal organizations must assign those values to complete the requirements.

## 03.13.11 Cryptographic Protection

Implement the following types of cryptography when used to protect the confidentiality of CUI: [Assignment: organization-defined types of cryptography].

## DISCUSSION

Cryptography is implemented in accordance with applicable laws, Executive Orders, directives, policies, regulations, standards, and guidelines. FIPS-validated cryptography is recommended for the protection of CUI.

## REFERENCES

Source Control: SC-13

Supporting Publications: FIPS 140-3 [38]

The term organization is used in many security requirements, and its meaning depends on context. For example, in a security requirement with an ODP, an organization can refer to either the federal agency or the nonfederal organization establishing the parameter values for the requirement.

Appendix C describes the security control tailoring criteria used to develop the security requirements and the results of the tailoring process. The appendix provides a list of controls from SP 800-53 that support the requirements and the controls that have been eliminated from the moderate baseline in accordance with the tailoring criteria.

## ASSESSING SECURITY REQUIREMENTS

SP 800-171A [84] provides a set of procedures to assess the security requirements described in this publication. The assessment procedures are based on the procedures described in SP 800-53A [57].

15 With few exceptions, the security controls in SP 800-53 are policy-, technology-, and sector-neutral, meaning that the controls focus on the fundamental measures necessary to protect information across the information life cycle.

## 3. The Security Requirements

This section describes 17 families of security requirements for protecting the confidentiality of CUI in nonfederal systems and organizations. When used in the context of the requirements in Sec. 3, the term system is defined to be nonfederal systems or system components that process, store, or transmit CUI or that provide protection for such systems or components. Not all security requirements mention CUI explicitly. However, the requirements are included because they directly affect the protection of CUI during processing, while in storage, and when in transmission between different locations.

Some systems, including specialized systems (e.g., industrial/process control systems, medical devices, computer numerical control machines), may have limitations on the application of certain security requirements. To accommodate such issues, the system security plan - as reflected in requirement 03.15.02 - is used to describe any enduring exceptions to the security requirements. Individual, isolated, or temporary deficiencies are managed though plans of action and milestones, as reflected in requirement 03.12.02.

## SCOPE AND APPLICABILITY OF SECURITY REQUIREMENTS

The security requirements in this section are only applicable to components of nonfederal systems that process, store, or transmit CUI or that provide protection for such components.

## [3.1. Access Control](https://csrc.nist.gov/projects/cprt/catalog#/cprt/framework/version/SP_800_53_5_1_1/home?element=AC)

## 03.01.01 Account Management

- a. Define the types of system accounts allowed and prohibited.
- b. Create, enable, modify, disable, and remove system accounts in accordance with policy, procedures, prerequisites, and criteria.
- c. Specify:
1. Authorized users of the system,
2. Group and role membership, and
3. Access authorizations (i.e., privileges) for each account.
- d. Authorize access to the system based on:
1. A valid access authorization and
2. Intended system usage.
- e. Monitor the use of system accounts.

- f. Disable system accounts when:
1. The accounts have expired,
2. The accounts have been inactive for [Assignment: organization-defined time period],
3. The accounts are no longer associated with a user or individual,
4. The accounts are in violation of organizational policy, or
5. Significant risks associated with individuals are discovered.
- g. Notify account managers and designated personnel or roles within:
1. [Assignment: organization-defined time period] when accounts are no longer required.
2. [Assignment: organization-defined time period] when users are terminated or transferred.
3. [Assignment: organization-defined time period] when system usage or the need-to-know changes for an individual.
- h. Require that users log out of the system after [Assignment: organization-defined time period] of expected inactivity or when [Assignment: organization-defined circumstances].

## DISCUSSION

This requirement focuses on account management for systems and applications. The definition and enforcement of access authorizations other than those determined by account type (e.g., privileged access, non-privileged access) are addressed in 03.01.02. System account types include individual, group, temporary, system, guest, anonymous, emergency, developer, and service. Users who require administrative privileges on system accounts receive additional scrutiny by personnel responsible for approving such accounts and privileged access. Types of accounts that organizations may prohibit due to increased risk include group, emergency, guest, anonymous, and temporary. Organizations may choose to define access privileges or other attributes by account, type of account, or a combination of both. Other attributes required for authorizing access include restrictions on the time of day, day of the week, and point of origin. When defining other system account attributes, organizations consider system requirements (e.g., system upgrades, scheduled maintenance) and mission and business requirements (e.g., time zone differences, remote access to facilitate travel requirements). Users who pose a significant security risk include individuals for whom reliable evidence indicates either the intention to use authorized access to the system to

cause harm or that adversaries will cause harm through them. Close coordination