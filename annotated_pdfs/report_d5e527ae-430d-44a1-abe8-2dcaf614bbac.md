<style>
.diff-container { font-family: 'Segoe UI', Roboto, sans-serif; font-size: 14px; line-height: 1.6; color: #000000 !important; background: #ffffff; padding: 16px; }
.diff-group { border-radius: 6px; margin: 12px 0; overflow: visible; position: relative; }
.diff-line { display: flex; align-items: flex-start; padding: 6px 10px; color: #000000 !important; }
.diff-line .gutter { flex: 0 0 24px; font-weight: bold; text-align: center; }
.diff-line .line-content { flex: 1; color: #000000 !important; }

/* Reason info icon — top-right corner of each group */
.reason-icon {
  position: absolute; top: 6px; right: 8px;
  width: 20px; height: 20px; border-radius: 50%;
  background: #6c757d; color: #fff !important;
  font-size: 12px; font-weight: bold; font-style: normal;
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; z-index: 2; flex-shrink: 0;
}
.reason-icon:hover { background: #495057; }
.reason-icon .reason-tooltip {
  display: none; position: absolute; top: 28px; right: 0;
  background: #212529; color: #fff !important; padding: 10px 20px;
  border-radius: 6px; font-size: 12px; font-weight: normal;
  white-space: normal; width: 500px; line-height: 1.3;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25); z-index: 10;
}
.reason-icon:hover .reason-tooltip { display: block; }

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
.diff-group[data-type="unchanged"] .diff-line .line-content { color: #000000 !important; }
</style>

<div class="diff-container">

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content"># COMMERCIAL RENTAL AGREEMENT</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">This Commercial Rental Agreement is made on 20 February 2026 between: LANDLORD: Mr. Rajesh Sharma Address: 12 MG Road, Bengaluru, Karnataka AND TENANT: ABC Tech Solutions Pvt Ltd Authorized Signatory: Ms. Priya Menon Address: 45 IT Park, Whitefield, Bengaluru, Karnataka Property Details: Type: Commercial Office Space Address: 3rd Floor, Commercial Complex, Indiranagar, Bengaluru</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   1\. Lease Term: The lease shall be valid for 36 months from the commencement date.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   2\. Monthly Rent: The Tenant agrees to pay Rs. 150000 per month.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   3\. Security Deposit: Tenant shall deposit Rs. 900000 refundable upon termination subject to deductions.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   4\. Lock-in Period: A mandatory lock-in period of 12 months shall apply.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   5\. Usage: The premises shall be used strictly for commercial/business purposes.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   6\. Maintenance & Repairs: Tenant shall handle interior maintenance; structural repairs shall be the responsibility of the Landlord.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   7\. Utilities & Taxes: Tenant shall bear electricity, water, GST (if applicable), and other statutory charges.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   8\. Late Payment Penalty: Delay beyond due date shall attract a penalty of Rs. 5000.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   9\. Termination: Either party may terminate the agreement by giving 90 days written notice.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   10\. Subleasing: Subleasing is prohibited without prior written consent of the Landlord.</div>
  </div>
</div>
<div class="diff-group" data-type="partial">
<span class="reason-icon">i<span class="reason-tooltip">The indemnity clause is present but does not fully encompass all aspects of the original clause content, specifically the mention of 'damages' and 'legal liabilities'.</span></span>
  <div class="diff-line deleted">
    <div class="gutter">&minus;</div>
    <div class="line-content"><span class="old-text">*   11\. Indemnity: Tenant agrees to indemnify the Landlord against losses arising from misuse of the premises.</span></div>
  </div>
  <div class="diff-line added">
    <div class="gutter">+</div>
    <div class="line-content">Tenant agrees to indemnify the Landlord against damages, misuse, and legal liabilities arising from the use of the premises.</div>
  </div>
</div>

<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   12\. Insurance: Tenant shall maintain appropriate commercial insurance coverage.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   13\. Force Majeure: Neither party shall be liable for delays due to natural calamities or events beyond control.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   14\. Dispute Resolution: Disputes shall be resolved through arbitration under the Arbitration and Conciliation Act, 1996 (India).</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   15\. Stamp Duty: Applicable stamp duty shall be borne by the Tenant as per Indian Stamp Act.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">*   16\. Registration: This Agreement shall be registered in Bengaluru as per Registration Act, 1908, if applicable.</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">NOTARY & WITNESS CLAUSE: IN WITNESS WHEREOF, the parties have executed this Agreement on the date first above written in the presence of the witnesses below. Landlord Signature: Tenant Signature: Witness 1 Name & Signature: Witness 2 Name & Signature:</div>
  </div>
</div>
<div class="diff-group" data-type="unchanged">
  <div class="diff-line normal">
    <div class="line-content">Notary Seal & Signature:</div>
  </div>
</div>
<div class="diff-group" data-type="new">
<span class="reason-icon">i<span class="reason-tooltip">The document does not contain any clause regarding rent escalation.</span></span>
  <div class="diff-line new-clause">
    <div class="gutter">+</div>
    <div class="line-content">Rent shall escalate by 8% after every 12 months.</div>
  </div>
</div>

<div class="diff-group" data-type="new">
<span class="reason-icon">i<span class="reason-tooltip">The document does not specify the permitted use as 'IT and Software Development Office only'.</span></span>
  <div class="diff-line new-clause">
    <div class="gutter">+</div>
    <div class="line-content">The premises shall be used strictly for IT and Software Development Office only.</div>
  </div>
</div>

<div class="diff-group" data-type="new">
<span class="reason-icon">i<span class="reason-tooltip">The governing law clause specifying the laws of India is completely absent from the document.</span></span>
  <div class="diff-line new-clause">
    <div class="gutter">+</div>
    <div class="line-content">This Agreement shall be governed by and construed in accordance with the laws of India.</div>
  </div>
</div>

</div>