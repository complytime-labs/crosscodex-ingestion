3.L-

## OUTCOME

Encryption is deployed to maintain confidentiality and integrity of sensitive data across the organization's network to protect from unauthorized access.

## RISK ADDRESSED

## SCOPE

Adversaries can position themselves between networked devices to enable network sniffing and data manipulation, or to steal operational data from environments for personal gain or future operations.

Passwords, credentials, secrets, and other sensitive or controlled information.

## NIST CSF 2.0 REFERENCE(S)

PR.DS-01, PR.DS-02, PR.DS-10

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AC-2, AC-3, AC-4, AU-9, AU-13, AU-16, CA-3, CP-9, MP-8, SA-8, SC-4, SC-7, SC-8, SC-11, SC-12, SC-13, SC-16, SC-24, SC-28, SC-32, SC-39, SC-40, SC-43, SI-3, SI-4, SI-7, SI10, SI-16

SP 800-82 Rev 3: AC-6, CM-2, CM-6, MP-1, PL-10, SA-8, SC-8, SC-13, SC-28

## ENABLE EMAIL SECURITY

## OUTCOME

Reduce risk from common email-based threats, such as spoofing, phishing, and interception.

## RISK ADDRESSED

## SCOPE

| Adversaries might send victims emails with malicious attachments or links, aiming to run harmful code on their systems. They can also conduct phishing through third-party services like social media platforms.   | All organizational email infrastructure.   |
|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------|

## NIST CSF 2.0 REFERENCE(S)

PR.DS-01, PR.DS-02, PR.DS-10

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AC-2, AC-3, AC-4, AU-9, AU-13, AU-16, CA-3, CP-9, MP-8, SA-8, SC-4, SC-7, SC-8, SC-11, SC-12, SC-13, SC-16, SC-24, SC-28, SC-32, SC-39, SC-40, SC-43, SI-3, SI-4, SI-7, SI10, SI-16

SP 800-82 Rev 3: AC-6, CM-2, CM-6, MP-1, PL-10, SA-8, SC-8, SC-13, SC-28

## RECOMMENDED ACTION

Use encryption, digital signatures, and cryptographic hashes to protect the confidentiality and integrity of network communications.

Identify critical electronic file types and data to protect while in transit and at rest. This may include personally identifiable information and sensitive, proprietary or trade secret information (e.g., PLC program code, robot programs, computer-aided drafting [CAD] or computer-aided manufacturing [MAC] files, operating manuals and documentation, electrical diagrams, network diagrams, historical production data).

Sensitive data, including passwords, are not electronically stored in plaintext anywhere in the organization and can only be accessed by authenticated and authorized users. Credentials are stored in a secure manner, such as with a credential/password manager.

OT: Use encryption for external connections and where latency issues would not result in an impact to operations.

| COST     | IMPACT   | EASE OF IMPLEMENTATION   |
|----------|----------|--------------------------|
| Moderate | High     | Complex                  |

## SUPPORT RESOURCES

[How to Protect the Data that is Stored on Your Devices](https://www.cisa.gov/resources-tools/training/how-protect-data-stored-your-devices)

## RECOMMENDED ACTION

On all corporate email infrastructure (1) STARTTLS is enabled, (2) Sender Policy Framework (SPF) and DomainKeys Identified Mail (DKIM) are enabled, and (3) Domain-based Message Authentication, Reporting, and Conformance (DMARC) is enabled and set to "reject."

| COST   | IMPACT   | EASE OF IMPLEMENTATION   |
|--------|----------|--------------------------|
| Low    | High     | Moderate                 |

## SUPPORT RESOURCES

[BOD 18-01: Enhance Email and Web Security CISA Insights - Enhance Email &amp; Web Security](https://www.cisa.gov/sites/default/files/publications/CISAInsights-Cyber-EnhanceEmailandWebSecurity_S508C-a.pdf)

## 3.N-

## DISABLE AUTORUN &amp; MACROS BY DEFAULT

## OUTCOME

Reduce the risk from embedded macros and similar executable code.

| RISK ADDRESSED                                                                                                                                    | SCOPE                      |
|---------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------|
| Adversaries rely on users to open malicious files to execute code. Social engineering tactics could be used to convince users to open such files. | All organizational assets. |

## NIST CSF 2.0 REFERENCE(S)

COST

IMPACT

EASE OF IMPLEMENTATION

Low Moderate Simple

PR.PS-01, ID.RA-07

## ADDITIONAL NIST REFERENCES

## RECOMMENDED ACTION

A system-enforced policy that disables macros, or similar embedded code, by default on all devices to prevent automatic execution of code or applications.

If macros must be enabled in specific circumstances, establish a policy for authorized users to request that macros are enabled on specific assets.

Autorun, or AutoPlay, should also be disabled by default to prevent unintentional code execution from sources such as USB or optical drives.

## SUPPORT RESOURCES

SP 800-53 Rev 5: CA-7, CM-1, CM-2, CM-3, CM-4, CM-5, CM-6, CM-7, CM-8, CM-9, CM-10, CM-11

SP 800-82 Rev 3: CM-1, CM-3, CM-4, CM-5, CM-9

[Using Caution with USB Drives Disable AutoRun Properly](https://www.cisa.gov/news-events/alerts/2009/01/20/microsoft-windows-does-not-disable-autorun-properly)

## ESTABLISH CHANGE MANAGEMENT PROCESSES

## RECOMMENDED ACTION

## OUTCOME

Policies and procedures exist to manage system changes and configurations.

## RISK ADDRESSED

## SCOPE

Delayed, insufficient, or incomplete ability to maintain or restore functionality of critical devices and service operations.

Organizational assets.

## NIST CSF 2.0 REFERENCE(S)

COST

IMPACT

## EASE OF IMPLEMENTATION

Moderate High Complex

PR.PS-01, PR.PS-02, PR.PS-03

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: CM-1, CM-2, CM-3, CM-4, CM-5, CM-6, CM-7, CM-8, CM-9, CM-10, CM-11, MA-3(06), SA-10(01), SA-10(03), SI-2, SI-7, SC-03(01), SC-39(01), SC-49, SC-51

SP 800-82 Rev 3: CM-1, CM-9, MA-1, MA-2, MA-6, SA-3, SA-22, SI-2, SI-3

Implement policies and processes to develop, document, and maintain secure change management for technology platforms and enforce configuration restrictions to prevent unauthorized changes.

Technical configuration change control processes are in place, prohibiting unauthorized changes unless approved. Test and document proposed changes in a non-production environment and analyze potential security impacts before implementation.

OT: Implement limited functionality by permitting only specific functions, protocols, and services necessary for OT operations.

## SUPPORT RESOURCES

[Configuration and Change Management](https://www.cisa.gov/sites/default/files/c3vp/crr_resources_guides/CRR_Resource_Guide-CCM.pdf)

[Importance of Configuration and Change Management to Security](https://niccs.cisa.gov/training/catalog/mentor-source-inc/importance-configuration-and-change-management-security)

## OUTCOME

Organizations reduce data loss and service disruption risks while efficiently managing, responding to, and recovering from incidents to maintain continuous service delivery.

## RISK ADDRESSED

## SCOPE

Adversaries can disrupt critical systems to halt the delivery of products or services. They can delete data and disable recovery services, preventing system recovery. Adversaries may turn off services designed to aid in recovering a corrupted system.

Organizational assets necessary for business operations.

## NIST CSF 2.0 REFERENCE(S)

PR.IR-01, DE.CM-01

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AC-2, AC-3, AC-4, AU-12, CA-7, CM-3, SC-4, SC-5, SC-7, SI-4

SP 800-82 Rev 3: AU-1, AU-2, SA-8, SC-1, SC-7(18), SI-1, SI-4, PL-8

[CISA Stop Ransomware Guide Cyber Guidance for Small Businesses](https://www.cisa.gov/cyber-guidance-small-businesses)

## MAINTAIN HARDWARE &amp; SOFTWARE APPROVAL PROCESS

## RECOMMENDED ACTION

## RECOMMENDED ACTION

Develop a list of all maintained backups, including installation media, license keys, configuration information, and backup retention period of the information.

Back up critical operations systems in near-real-time, and frequently back up all systems necessary for operations on a regular schedule consistent with the needs of the organization.

Securely store backups offsite and offline. Test backups and recovery on a recurring basis, no less than once per year.

Before initiating restoration, validate the integrity of backups and other assets intended for restoration. This verification process is to ensure that data is intact, accurate, and reliable, minimizing the risk of data corruption during the restoration process.

Check restoration assets for indicators of compromise, file corruption, and other integrity issues before use.

Regularly test backup information to verify media reliability and information integrity.

OT: Stored information for OT assets includes, at a minimum, device configurations, roles, engineering drawings, and tools.

COST

IMPACT

## EASE OF IMPLEMENTATION

High High Moderate

## SUPPORT RESOURCES

## OUTCOME

Increase visibility into deployed technology assets and reduce the likelihood of breach by users installing unapproved hardware, firmware, or software.

## RISK ADDRESSED

## SCOPE

Adversaries can manipulate products or delivery mechanisms before they reach final users and attempt data or system compromise. They can target devices that move across industrial control systems and production networks.

Organizational assets.

## NIST CSF 2.0 REFERENCE(S)

COST

IMPACT

## EASE OF IMPLEMENTATION

Moderate High Moderate

## SUPPORT RESOURCES

[Securing the Software Supply Chain](https://www.cisa.gov/resources-tools/resources/securing-software-supply-chain-recommended-practices-guide-customers-and)

25

PR.PS-02, PR.PS-03, ID.RA-07

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: CA-7, CM-3, CM-4, CM-7(09), CM-11, MA3(06), SA-10(01), SC-3(01), SC-39(01), SC-49, SC-51, SI-2, SI-7 SP 800-82 Rev 3: CM-3, CM-4, CM-5, MA-1, MA-2, MA-6, SA-3, SA-22, SI-2, SI-3

Implement an administrative policy and process that requires review, testing, and approval before new hardware, firmware, or software is installed or deployed.

Organizations maintain a list of approved hardware, firmware, and software that includes specification of approved versions, when technically feasible.

OT: Consider additional requirements for organizations with OT environments when deploying patches and updates. This includes testing and validation to ensure they do not impact operational capabilities or safety.

3.R-

## OUTCOME

Enhance visibility to detect and respond to cyber incidents while ensuring security logs are protected from unauthorized access and tampering.

## RISK ADDRESSED

## SCOPE

Delayed, insufficient, or incomplete ability to detect and respond to potential cyber incidents.

Organizational assets on all assets, where safe and technically feasible.

## RECOMMENDED ACTION

Administrative and security-focused logs (e.g., operating systems, applications, and services; intrusion detection systems/intrusion prevention systems; firewalls; data loss prevention; virtual private networks) are collected and stored for use in both detection and incident response activities (e.g., forensics).

Logs are stored in a central system, such as a security information and event management tool or central database, and can only be accessed or modified by authorized and authenticated users. Logs are stored for a duration informed by risk or pertinent regulatory guidelines.

Security teams are notified when a critical log function is disabled.

OT: For OT assets where logs are non-standard or not available, network traffic and communications between those assets and other assets is collected.

| NIST CSF 2.0 REFERENCE(S)   | COST     | IMPACT   | EASE OF IMPLEMENTATION   |
|-----------------------------|----------|----------|--------------------------|
| PR.PS-04                    | Moderate | High     | Moderate                 |

## SUPPORT RESOURCES

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AU-2, AU-3, AU-6, AU-7, AU-11, AU-12 SP 800-82 Rev 3: AU-1, AU-3, SI-4

Best Practices for Event Logging and Threat Detection Guide to Computer Security Log Management Improving Investigative and Remediation Capabilities

## PROHIBIT CONNECTION OF UNAUTHORIZED DEVICES

## RECOMMENDED ACTION

## OUTCOME

Prevent malicious actors from achieving initial access or data exfiltration via unauthorized portable media devices.

## RISK ADDRESSED

## SCOPE

Adversaries might infiltrate systems, including disconnected or air-gapped networks, by copying malware to removable media such as USB drives.

Organizational assets.

## NIST CSF 2.0 REFERENCE(S)

PR.DS-01

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: CA-3, CP-9, MP-8, SC-4, SC-7, SC-12, SC-13, SC-28, SC-32, SC-39, SC-43, SI-3, SI-4, SI-7 SP 800-82 Rev 3: MP-1, SC-8(1), SC-13, SC-28

Organizations maintain policies and processes to ensure that unauthorized media and hardware are not connected to IT and OT assets, such as limiting use of USB devices and removable media.

OT: When feasible, establish procedures to remove, disable, or otherwise secure physical ports to prevent the connection of unauthorized devices or establish procedures for granting access through approved exceptions.

| COST     | IMPACT   | EASE OF IMPLEMENTATION   |
|----------|----------|--------------------------|
| Moderate | High     | Complex                  |

## SUPPORT RESOURCES

[Using Caution with USB Drives](https://www.cisa.gov/news-events/news/using-caution-usb-drives)

[Proposed Security Requirements for Restricted Transactions](https://www.cisa.gov/sites/default/files/2024-10/Proposed-Security-Requirements-EO-14117-21Oct24508.pdf)

## SECURE INTERNET-FACING DEVICES

## OUTCOME

Unauthorized users cannot gain an initial system foothold by exploiting known weaknesses in internet-facing assets.

## RISK ADDRESSED

## SCOPE

Organizational assets on the public internet.

Adversaries might exploit weaknesses in internet-facing hosts or systems to gain initial network access, targeting software bugs, temporary glitches, or misconfigurations.

## NIST CSF 2.0 REFERENCE(S)

PR.IR-01

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AC-3, AC-4, SC-4, SC-5, SC-7 SP 800-82 Rev3: PL-8, SA-8, SC-1, SC-7(18), SI-1

## RECOMMENDED ACTION

Minimize internet-facing assets whenever possible. Prioritize keeping software current with timely patches and updates. If unable to apply updates, consider removing that asset, or implement compensating controls to prevent common forms of exploitation. These controls may include network segmentation or firewalls.

All operating system applications, software and network protocols that are not necessary for mission-critical applications are disabled on internet-facing assets.

Network management interfaces (NMIs) should never be exposed to the public internet and should only be accessible from within enterprise networks.

Logically segment enterprise networks and production networks, including cloudbased platforms, according to trust boundaries and platform types (e.g., IT, IoT, OT, mobile, guests), and only permit required communications between segments.

| COST     | IMPACT   | EASE OF IMPLEMENTATION   |
|----------|----------|--------------------------|
| Moderate | High     | Complex                  |

## SUPPORT RESOURCES

Remediate Vulnerabilities for Internet-Accessible Systems Internet Exposure Reduction Guidance

[Mitigating the Risk from Internet-Exposed Management Interfaces](https://www.cisa.gov/news-events/directives/bod-23-02-implementation-guidance-mitigating-risk-internet-exposed-management-interfaces)

<!-- image -->

4.A-

## ESTABLISH MALICIOUS CODE DETECTION

## OUTCOME

4.B-

<!-- image -->

## DETECT

## RECOMMENDED ACTION

Implement signature-based mechanisms (relying on known patterns or signatures of malicious code used by antivirus software to identify and block threats) and non-signature-based mechanisms (focusing behavior, heuristics, or anomalies) to detect and eradicate malicious code at system endpoints. Ensure antivirus software is updated, active, and configured to automatically scan emails and removable media (e.g., flash drives) for ransomware and other malware.

OT: Using antivirus software with OT devices may require special practices, including compatibility checks, change management, and performance impact metrics. These practices should be employed to test new signatures and new versions of malicious code protection solutions.

Enables early threat identification, strengthens system integrity, provides insights for faster remediation, and minimizes downtime.

## RISK ADDRESSED

## SCOPE

Organization-wide.

Malicious software can involve payloads, droppers, backdoors, etc. Adversaries use malware to control remote machines, evade defenses, and execute postcompromise actions.

## NIST CSF 2.0 REFERENCE(S)

COST

IMPACT

EASE OF IMPLEMENTATION

Moderate High Moderate

DE.CM-09

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: AC-4, AC-9, AU-12, CA-7, CM-3, CM-6, CM-10,

CM-11, SC-34, SC-35, SI-4, SI-7

SP 800-82 Rev 3: AU-1, MP-2, SI-3, SI-4, SI-7

## IDENTIFY ADVERSE EVENTS

## OUTCOME

Organizations can identify adverse security events.

## RISK ADDRESSED

## SCOPE

Initial access, privilege escalation, and lateral movement.

Organization-wide.

## NIST CSF 2.0 REFERENCE(S)

COST

IMPACT

## EASE OF IMPLEMENTATION

Moderate High Complex

## SUPPORT RESOURCES

[Planning Considerations for Cyber Incidents Cybersecurity Incident &amp; Vulnerability Response Playbooks](https://www.cisa.gov/sites/default/files/2024-08/Federal_Government_Cybersecurity_Incident_and_Vulnerability_Response_Playbooks_508C.pdf)

[Continuously Hunt for Network Intrusions](https://media.defense.gov/2019/Sep/09/2002180360/-1/-1/0/Continuously%20Hunt%20for%20Network%20Intrusions%20-%20Copy.pdf)

DE.AE-08

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: IR-4, IR-8

SP 800-82 Rev 3: IR-4

## SUPPORT RESOURCES

Ensure Your OS Antivirus and Anti-Malware Protections are Active Control System Defense: Know the Opponent

## RECOMMENDED ACTION

Ensure the organization has defined clear criteria and processes for adverse events. If an adverse event is suspected, follow the protocol outlined in the incident response plan to escalate the situation.

Automate event information analysis as much as possible to accelerate the investigative timeline for managing suspected adverse events. This will give analysts the time and capacity to mitigate these events effectively.

Conduct analyst role-specific training on the proper protocols and procedures to follow in the event of a suspected cyber incident.

OT: Organizations should account for OT-specific events and anomalies in their processes and environments. It's important to recognize that certain tools and alerts for behaviors or events that could indicate an intrusion might actually be normal within the OT environment.

<!-- image -->

5.A-

5.B-

## RESPOND RESPOND

## ESTABLISH INCIDENT COMMUNICATION PROCEDURES

## RECOMMENDED ACTION

Coordinate crisis communication methods between internal and external organization partners and critical suppliers.

Design a communications plan that identifies stakeholders and mechanisms for coordination and communications during an incident.

Collaborate with stakeholders and securely share information consistent with response plans and information-sharing agreements. Priorities for sharing information include preventing the spread of infections to other systems and networks.

Regularly update senior leadership on the status of major incidents.

Notify human resources when malicious insider activity occurs.

Establish and follow media communications procedures for incident response that comply with the organization's policies on media interaction and information disclosure.

## OUTCOME

## RISK ADDRESSED

## SCOPE

Without established communication procedures, incidents can disrupt coordination among response teams, slowing incident resolution, increasing downtime, and amplifying overall damage.

| NIST CSF 2.0 REFERENCE(S)   | COST   | IMPACT   | EASE OF IMPLEMENTATION   |
|-----------------------------|--------|----------|--------------------------|
| RS.CO-03                    | Low    | High     | Moderate                 |

Organization-wide.

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: IR-4, IR-6, IR-7, SR-3, SR-8 SP 800-82 Rev 3: IR-4, IR-6

Guidance on effective communications in a cyber incident Incident Management

## SUPPORT RESOURCES

## ESTABLISH INCIDENT REPORTING PROCEDURES

## RECOMMENDED ACTION

## OUTCOME

CISA and other organizations are better able to provide assistance or understand the broader scope of a cyber incident.

## RISK ADDRESSED

## SCOPE

Without timely incident reporting, CISA and other groups are less able to assist affected organizations and lack critical insight into the broader threat landscape, such as whether a broader attack is occurring against a specific sector.

Organization-wide.

## NIST CSF 2.0 REFERENCE(S)

RS.CO-02, RS.MA-01

## ADDITIONAL NIST REFERENCES

SP 800-53 Rev 5: IR-4, IR-6, IR-7, IR-8, SR-3, SR-8

SP 800-82 Rev 3: IR-4, IR-6, IR-8

Organizations maintain policy and procedures on to whom and how to report all

confirmed cybersecurity incidents to appropriate external entities (e.g.,

state/federal regulators or sector risk management agencies [SRMAs] as required, information sharing and analysis centers [ISACs], information sharing and analysis organizations [ISAOs], and CISA).

Known incidents are reported to CISA as well as other necessary parties within time frames directed by applicable regulatory guidance or in the absence of guidance, as soon as safely feasible.

| COST     | IMPACT   | EASE OF IMPLEMENTATION   |
|----------|----------|--------------------------|
| Moderate | High     | Moderate                 |

## SUPPORT RESOURCES

Cybersecurity Incident Response Critical Infrastructure Threat Information Sharing Framework Cyber Incident Reporting

<!-- image -->

6.A-

## EXECUTE INCIDENT RECOVERY PLAN

| OUTCOME                                                                                                                                                                                                                                                                                                                                                                                | OUTCOME                                                                                                                                                                                                                                                                                                                                                                                | RECOMMENDED ACTION                                                                                                                                                                                                                                                                                                   | RECOMMENDED ACTION                                                                                                                                                                                                                                                                                                   | RECOMMENDED ACTION                                                                                                                                                                                                                                                                                                   |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Organizations are capable of safely and effectively recovering from a cybersecurity incident.                                                                                                                                                                                                                                                                                          | Organizations are capable of safely and effectively recovering from a cybersecurity incident.                                                                                                                                                                                                                                                                                          | Execute plans to recover and restore service to business- or mission-critical assets or systems that might be impacted by a cybersecurity incident. This may                                                                                                                                                         | Execute plans to recover and restore service to business- or mission-critical assets or systems that might be impacted by a cybersecurity incident. This may                                                                                                                                                         | Execute plans to recover and restore service to business- or mission-critical assets or systems that might be impacted by a cybersecurity incident. This may                                                                                                                                                         |
| RISK ADDRESSED                                                                                                                                                                                                                                                                                                                                                                         | include the ability to execute mission essential functions in a degraded manner without access to critical assets or even internet access (e.g., shift to paper- SCOPE                                                                                                                                                                                                                 | based operations,                                                                                                                                                                                                                                                                                                    | communications,                                                                                                                                                                                                                                                                                                      | etc.)                                                                                                                                                                                                                                                                                                                |
| Disruption to the availability of an asset, service, or system.                                                                                                                                                                                                                                                                                                                        | Organizational assets.                                                                                                                                                                                                                                                                                                                                                                 | Complete post-incident analysis to identify areas for improvement and refine the incident response plan. Focus on incorporating lessons learned, enhancing detection and response capabilities, updating policies and procedures including training, and ensuring that all stakeholders are informed of the changes. | Complete post-incident analysis to identify areas for improvement and refine the incident response plan. Focus on incorporating lessons learned, enhancing detection and response capabilities, updating policies and procedures including training, and ensuring that all stakeholders are informed of the changes. | Complete post-incident analysis to identify areas for improvement and refine the incident response plan. Focus on incorporating lessons learned, enhancing detection and response capabilities, updating policies and procedures including training, and ensuring that all stakeholders are informed of the changes. |
| NIST CSF 2.0 REFERENCE(S)                                                                                                                                                                                                                                                                                                                                                              | NIST CSF 2.0 REFERENCE(S)                                                                                                                                                                                                                                                                                                                                                              | COST                                                                                                                                                                                                                                                                                                                 | IMPACT                                                                                                                                                                                                                                                                                                               | EASE OF IMPLEMENTATION                                                                                                                                                                                                                                                                                               |
| RC.RP-01, ID.IM-02, ID.IM-04                                                                                                                                                                                                                                                                                                                                                           |                                                                                                                                                                                                                                                                                                                                                                                        | Moderate                                                                                                                                                                                                                                                                                                             | High                                                                                                                                                                                                                                                                                                                 | Complex                                                                                                                                                                                                                                                                                                              |
| ADDITIONAL NIST REFERENCES                                                                                                                                                                                                                                                                                                                                                             | ADDITIONAL NIST REFERENCES                                                                                                                                                                                                                                                                                                                                                             | SUPPORT RESOURCES                                                                                                                                                                                                                                                                                                    | SUPPORT RESOURCES                                                                                                                                                                                                                                                                                                    | SUPPORT RESOURCES                                                                                                                                                                                                                                                                                                    |
| SP 800-53 Rev 5: AC-1, AT-1, AU-1, CA-1, CM-1, CP-1, CP-2, CP- 10, IA-1, IR-1, IR-4, IR-8, MA-1, MP-1, PE-1, PL-1, PM-1, PS-1, PT- 1, RA-1, SA-1, SC-1, SI-1, SR-1, CA-2, CA-5, CA-7, CA-8, CP-2, CP- 4, IR-3, IR-4, IR-8, PL-2, PM-4, PM-31, RA-3, RA-5, RA-7, SA-8, SA-11, SI-2, SI-4, SR-2, SR-5 SP 800-82 Rev 3: CA-2, CA-5, CP-4, CP-1, CP-2, CP-10, IR-1,IR-8, RA-3, SA-11, SR-6 | SP 800-53 Rev 5: AC-1, AT-1, AU-1, CA-1, CM-1, CP-1, CP-2, CP- 10, IA-1, IR-1, IR-4, IR-8, MA-1, MP-1, PE-1, PL-1, PM-1, PS-1, PT- 1, RA-1, SA-1, SC-1, SI-1, SR-1, CA-2, CA-5, CA-7, CA-8, CP-2, CP- 4, IR-3, IR-4, IR-8, PL-2, PM-4, PM-31, RA-3, RA-5, RA-7, SA-8, SA-11, SI-2, SI-4, SR-2, SR-5 SP 800-82 Rev 3: CA-2, CA-5, CP-4, CP-1, CP-2, CP-10, IR-1,IR-8, RA-3, SA-11, SR-6 | Incident Response Training Cybersecurity Incident & Vulnerability Response Playbook Incident Response Plan (IRP) Basics                                                                                                                                                                                              | Incident Response Training Cybersecurity Incident & Vulnerability Response Playbook Incident Response Plan (IRP) Basics                                                                                                                                                                                              | Incident Response Training Cybersecurity Incident & Vulnerability Response Playbook Incident Response Plan (IRP) Basics                                                                                                                                                                                              |

## RECOVER