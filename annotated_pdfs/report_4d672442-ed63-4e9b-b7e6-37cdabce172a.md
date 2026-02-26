<style>
.diff-container { font-family: 'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.6; color: #FFFFFF !important; }
.diff-group { border-radius: 6px; margin: 12px 0; overflow: hidden; }
.diff-line { display: flex; align-items: flex-start; padding: 6px 10px; color: #FFFFFF !important; }
.diff-line .gutter { flex: 0 0 24px; font-weight: bold; text-align: center; }
.diff-line .line-content { flex: 1; color: #FFFFFF !important; }

/* Violation (modified) — deleted = red, added = green */
.diff-group[data-type="modified"] .diff-line.deleted { background-color: #fde8e8; }
.diff-group[data-type="modified"] .diff-line.deleted .gutter { color: #dc3545 !important; }
.diff-group[data-type="modified"] .diff-line.deleted .old-text { color: #6b1015 !important; text-decoration: line-through; }
.diff-group[data-type="modified"] .diff-line.added { background-color: #d4edda; }
.diff-group[data-type="modified"] .diff-line.added .gutter { color: #28a745 !important; }
.diff-group[data-type="modified"] .diff-line.added .line-content { color: #155724 !important; }

/* Partially satisfied — deleted = orange, added = green */
.diff-group[data-type="partial"] .diff-line.deleted { background-color: #fff3cd; }
.diff-group[data-type="partial"] .diff-line.deleted .gutter { color: #fd7e14 !important; }
.diff-group[data-type="partial"] .diff-line.deleted .old-text { color: #856404 !important; text-decoration: line-through; }
.diff-group[data-type="partial"] .diff-line.added { background-color: #d4edda; }
.diff-group[data-type="partial"] .diff-line.added .gutter { color: #28a745 !important; }
.diff-group[data-type="partial"] .diff-line.added .line-content { color: #155724 !important; }

/* Not found — new clause = blue */
.diff-group[data-type="new"] .diff-line.new-clause { background-color: #e8f0fe; }
.diff-group[data-type="new"] .diff-line.new-clause .gutter { color: #0d6efd !important; }
.diff-group[data-type="new"] .diff-line.new-clause .line-content { color: #0a3577 !important; }

/* Unchanged — match and normal: no bg, pure black text */
.diff-group[data-type="unchanged"] .diff-line .line-content { color: #FFFFFF !important; }
</style>

<div class="diff-container">

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">COMMERCIAL RENTAL AGREEMENT</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">This Commercial Rental Agreement is made on 20 February 2026 between: LANDLORD: Mr.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">Rajesh Sharma Address: 12 MG Road, Bengaluru, Karnataka AND TENANT: ABC Tech Solutions</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">Pvt Ltd Authorized Signatory: Ms. Priya Menon Address: 45 IT Park, Whitefield, Bengaluru,</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">Karnataka Property Details: Type: Commercial Office Space Address: 3rd Floor, Commercial</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">Complex, Indiranagar, Bengaluru</div>
  </div>
</div>
<div class="diff-group" data-type="partial" data-tooltip="The document specifies a 36‑month lease term but does not state the required start date of 01 April 2026.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">1. Lease Term: The lease shall be valid for 36 months from the commencement date.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Lease Term: The lease shall be for a period of 36 months commencing on 01 April 2026 and ending on 31 March 2029.</div>
  </div>
</div>

<div class="diff-group" data-type="modified" data-tooltip="The document's Monthly Rent clause specifies Rs. 150,000 with no due date, which contradicts the required amount of Rs. 2,25,000 payable on or before the 5th of each month.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">2. Monthly Rent: The Tenant agrees to pay Rs. 150000 per month.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Monthly Rent: The Tenant shall pay Rs. 2,25,000 payable on or before the 5th of every month.</div>
  </div>
</div>

<div class="diff-group" data-type="modified" data-tooltip="The Security Deposit amount in the document (Rs. 900,000) does not match the required amount (Rs. 13,50,000).">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">3. Security Deposit: Tenant shall deposit Rs. 900000 refundable upon termination subject to deductions.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Security Deposit: Tenant shall deposit Rs. 13,50,000 refundable upon termination subject to deductions.</div>
  </div>
</div>

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">4. Lock-in Period: A mandatory lock-in period of 12 months shall apply.</div>
  </div>
</div>
<div class="diff-group" data-type="modified" data-tooltip="The document only restricts use to "commercial/business purposes" which is broader than the required specific permitted use of "IT and Software Development Office only". This contradicts the clause requirement.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">5. Usage: The premises shall be used strictly for commercial/business purposes.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Permitted Use: The premises shall be used solely for IT and Software Development Office activities only.</div>
  </div>
</div>

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">6. Maintenance & Repairs: Tenant shall handle interior maintenance; structural repairs shall be</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">the responsibility of the Landlord.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">7. Utilities & Taxes: Tenant shall bear electricity, water, GST (if applicable), and other statutory</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">charges.</div>
  </div>
</div>
<div class="diff-group" data-type="modified" data-tooltip="The clause exists but specifies a penalty of Rs. 5,000 with no grace period, which contradicts the required Rs. 10,000 after a 7‑day grace period.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">8. Late Payment Penalty: Delay beyond due date shall attract a penalty of Rs. 5000.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Late Payment Penalty: If payment is not made within 7 days after the due date, the Tenant shall pay a penalty of Rs. 10,000.</div>
  </div>
</div>

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">9. Termination: Either party may terminate the agreement by giving 90 days written notice.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">10. Subleasing: Subleasing is prohibited without prior written consent of the Landlord.</div>
  </div>
</div>
<div class="diff-group" data-type="partial" data-tooltip="Clause mentions indemnity for losses due to misuse but does not explicitly cover damages and legal liabilities as required.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">11. Indemnity: Tenant agrees to indemnify the Landlord against losses arising from misuse of the premises.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Tenant shall indemnify and hold harmless the Landlord from and against any and all damages, losses, costs, expenses, and legal liabilities arising out of the Tenant's misuse of the premises, including any third‑party claims.</div>
  </div>
</div>

<div class="diff-group" data-type="partial" data-tooltip="The document includes an insurance clause, but it does not specify fire and liability insurance nor the required coverage amount of Rs. 50,00,000.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">12. Insurance: Tenant shall maintain appropriate commercial insurance coverage.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Tenant shall maintain fire and liability insurance coverage in the amount of Rs. 50,00,000 throughout the lease term.</div>
  </div>
</div>

<div class="diff-group" data-type="partial" data-tooltip="Clause mentions natural calamities and events beyond control but does not explicitly include government restrictions as required.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">13. Force Majeure: Neither party shall be liable for delays due to natural calamities or events beyond control.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Force Majeure: Neither party shall be liable for delays or failures to perform arising from natural calamities, government restrictions, or other unforeseen events beyond the reasonable control of the parties.</div>
  </div>
</div>

<div class="diff-group" data-type="partial" data-tooltip="The dispute resolution clause is present but does not specify the required arbitration location (Hyderabad).">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">14. Dispute Resolution: Disputes shall be resolved through arbitration under the Arbitration and Conciliation Act, 1996 (India).</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Dispute Resolution: All disputes arising out of or in connection with this Agreement shall be finally resolved by arbitration in Hyderabad, India, in accordance with the Arbitration and Conciliation Act, 1996. The arbitration shall be conducted by a sole arbitrator appointed mutually by the parties.</div>
  </div>
</div>

<div class="diff-group" data-type="modified" data-tooltip="Clause exists but references the Indian Stamp Act instead of the required Telangana Stamp Act.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">15. Stamp Duty: Applicable stamp duty shall be borne by the Tenant as per Indian Stamp Act.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Stamp Duty: The stamp duty shall be borne by the Tenant as per the Telangana Stamp Act.</div>
  </div>
</div>

<div class="diff-group" data-type="modified" data-tooltip="Clause exists but mandates registration in Bengaluru and adds a conditional 'if applicable', which contradicts the required registration at Hyderabad Sub-Registrar Office.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">16. Registration: This Agreement shall be registered in Bengaluru as per Registration Act, 1908, if applicable.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Registration: This Agreement shall be registered at the Hyderabad Sub-Registrar Office in accordance with the Registration Act, 1908.</div>
  </div>
</div>

<div class="diff-group" data-type="partial" data-tooltip="Document includes a Notary & Witness clause with a place for Notary seal and signature, but does not explicitly state that the agreement shall be notarized before a registered Notary Public as required.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">NOTARY & WITNESS CLAUSE: IN WITNESS WHEREOF, the parties have executed this</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Notary Clause: This Agreement shall be notarized before a registered Notary Public, and the Notary's seal and signature shall appear below.</div>
  </div>
</div>

<div class="diff-group" data-type="partial" data-tooltip="The document includes a witness clause with two witnesses, but it does not require or mention that the witnesses present valid ID proof as stipulated in the clause content.">
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">Agreement on the date first above written in the presence of the witnesses below. Landlord Signature: ___________________________ Tenant Signature: ___________________________ Witness 1 Name & Signature: ___________________________ Witness 2 Name & Signature: ___________________________ Notary Seal & Signature: ___________________________</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Witness Clause: The Agreement shall be signed in the presence of two independent witnesses, each of whom must provide a valid government‑issued ID proof (e.g., Aadhaar, PAN, Passport) at the time of signing. Witness 1 Name, Signature, and ID Number: ___________________________ Witness 2 Name, Signature, and ID Number: ___________________________</div>
  </div>
</div>

<div class="diff-group" data-type="new" data-tooltip="The document does not contain any provision regarding rent escalation or periodic rent increase.">
  <div class="diff-line new-clause">
    <div class="gutter">+</div>
    <div class="line-content">Rent Escalation: The rent shall increase by 8% after every 12 months of the lease term. The increased rent shall become payable from the first day of the month following each anniversary of the commencement date. The Tenant shall be notified in writing of the new rent amount at least 30 days prior to the effective date of the increase.</div>
  </div>
</div>

<div class="diff-group" data-type="new" data-tooltip="The document does not contain a Governing Law clause specifying that the agreement is governed by the laws of India.">
  <div class="diff-line new-clause">
    <div class="gutter">+</div>
    <div class="line-content">Governing Law: This Agreement shall be governed by and construed in accordance with the laws of India.</div>
  </div>
</div>

</div>