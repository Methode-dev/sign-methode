============
Méthode Sign
============

Simple electronic signature of PDFs (invoices, contracts...) for Odoo 19,
built to produce evidence that holds: one-time codes, server-side fields,
RFC 3161 timestamps, a hash-chained audit trail and a PAdES seal.

Signature level
===============

Documents are signed with the company's **electronic seal certificate**
(PKCS#12) as PAdES signatures, one per signer, plus a document timestamp and
validation data: **PAdES B-LTA**. Each signer is identified by a one-time code
(e-mail or SMS) and each signing act is timestamped by the configured TSA.

This is an *advanced* electronic signature (eIDAS AdES). A *qualified*
signature (QES) additionally requires a qualified certificate issued to each
signer by a qualified trust service provider after an identity check; the
``res.company._sign_methode_seal_signer`` / ``_sign_methode_timestamper`` hooks
are where such a provider would plug in.

Flow
====

#. **Send**: from any signable record ("Send for signature") or from
   *Signatures > Requests* with any PDF. By default a signature page is added at
   the end of the document, so there is nothing to place. A template places
   fields on the pages instead (drag and drop editor).
#. **Sign**: the signer opens their link, enters the code, and the page leads
   them field by field ("Next", "Go to signature"). The signature is adopted
   once (drawn, typed or uploaded) and applied everywhere it is needed.
#. **Done**: when everyone has signed, the visuals are stamped, a certificate
   of completion is appended, the file is sealed and e-mailed to everyone, and
   the signer can download it from the same page.

Evidence
========

* The source file and the exact document shown to signers are kept, with
  their SHA-256.
* Field placement and requirements live on the server; the browser only
  sends values.
* Every event (sent, opened, code sent/verified/failed, signed, completed,
  downloaded...) is recorded with time, IP and user agent, each hash-chained
  to the previous one; the request shows whether the chain is intact.
* Links carry a random token whose hash only is stored; each e-mail issues a
  new one.

Configuration
=============

*Signatures > Configuration > Settings*: upload the seal certificate and its
password, set the timestamp authority URL (a qualified TSA in production),
and the default code channel, validity and reminders.

Integrations
============

Inherit ``sign.methode.mixin`` and override as needed:

* ``_sign_get_document()`` returns ``(filename, pdf_bytes)``; by default the
  model's first PDF report, else its latest PDF attachment;
* ``_sign_get_signers()`` returns ``[{"partner_id", "role_id"}]``;
* ``_sign_on_completed(request)`` / ``_sign_on_declined(request)``.

Bridges: ``sign_methode_account`` (invoices), ``sign_methode_agreement``
(OCA agreements, AGPL), ``sign_methode_sms`` (SMS codes) and, in daf-uw-erp,
``daf_sign`` (brokerage firm partnership agreements).

Dependencies
============

``pyhanko`` (MIT), ``pypdf`` and ``reportlab``. On Ubuntu noble, keep
pyHanko 0.21 (it accepts the apt ``cryptography`` 41) and install ``oscrypto``
from its master branch (the PyPI release fails on OpenSSL 3.0.1x); see the
daf-uw-erp Dockerfile.
