<!-- Attach-kind reference body (ADR-0003). Rendered for negotiated-MSA subcontract profiles
     (manifest kind="attach") IN PLACE OF the 27-article library body: a one-page reference built from
     ONLY the standard body's own VERBATIM fragments — the parties/date/project preamble and the §2.1
     Contract Price sentences — plus the profile's manifest render_line (a fixed reference line) and the
     standard signature block. It contains NO independently-drafted terms: every line is either a
     verbatim standard-body fragment (see standard_subcontract_v1.md) or a token, so NO per-version
     legal_review gate applies — there is no attestable body language of its own (cf.
     subcontracts/terms.py load_attach_reference). The binding terms are the externally-negotiated
     Master Subcontract Agreement the render_line references; Exhibit A (scope) and Annex C (Schedule of
     Values) render as their own package files. IMMUTABLE + sha256-pinned by
     subcontracts/terms._ATTACH_REFERENCE_SHA256 (a wording change updates that pin in the SAME commit).
     {{tokens}} are filled per-deal by subcontract_generate (STRICT). -->

SUBCONTRACT AGREEMENT

THIS AGREEMENT, made this {{agreement_date}}, by and between {{contractor_entity}}, hereinafter “Contractor,” and {{subcontractor_entity}}, hereinafter “Subcontractor,” each a “Party” and collectively “the Parties” for the following Project:  {{project_name}} on which the Prime Contractor is {{prime_contractor}} and the “Owner” is {{owner_entity}}.  The Contract between the Contractor and the Owner is the “Prime Contract.”

The Contract Price shall be the total amount payable by Contractor to Subcontractor for all Work or other activity arising from or related to this Subcontract. The Contract Price is {{contract_price_clause}}, subject to additions and deductions by Change Order or other provisions hereof.

{{render_line}}

Witness the following signatures and seals:

{{signature_entity}}	                                       SUBCONTRACTOR

BY:	_______________________________	BY:__________________________________
Name and Capacity Printed:			Name and Capacity Printed:
______________________________________	_____________________________________
Dated:	_______________________________	Dated:	_______________________________
