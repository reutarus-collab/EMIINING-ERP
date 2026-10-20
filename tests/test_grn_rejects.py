import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_grpo_and_customers import Base          # same PO/GRN harness the retry tests use
from services.models import GoodsReceiptLine, GoodsReceiptNote, PurchaseOrderLine, SupplierTxn


class GRNRejectedGoodsTests(Base):
    def rejected_only(self, bags):
        return {'payment_method': 'ON_ACCOUNT', 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': 0, 'qty_rejected_po_uom': bags, 'qty_kg_accepted': 0}]}

    def test_rejecting_a_whole_delivery_does_not_crash_or_add_stock(self):
        # Regression: "float division by zero" (price per kg = value / 0 kg accepted)
        r = self.post(str(uuid.uuid4()), self.rejected_only(3))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.stock(), 0.0)
        line = PurchaseOrderLine.query.get(self.line_id)
        self.assertEqual((line.qty_received, line.qty_rejected), (0, 3))
        self.assertEqual(GoodsReceiptNote.query.count(), 1)
        self.assertEqual(GoodsReceiptLine.query.one().qty_accepted, 0.0)
        self.assertEqual(SupplierTxn.query.count(), 0)            # nothing was accepted, so nothing is owed

    def test_partly_accepted_partly_rejected_still_adds_only_the_accepted_stock(self):
        body = {'payment_method': 'ON_ACCOUNT', 'received_items': [
            {'line_id': self.line_id, 'qty_po_uom': 4, 'qty_rejected_po_uom': 2, 'qty_kg_accepted': 200}]}
        r = self.post(str(uuid.uuid4()), body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.stock(), 200.0)
        self.assertEqual(PurchaseOrderLine.query.get(self.line_id).qty_rejected, 2)
        self.assertEqual(SupplierTxn.query.one().amount, 8000.0)  # 4 bags x 2000, rejected bags are not paid for

    def test_rejects_cannot_push_the_line_past_the_order(self):
        r = self.post(str(uuid.uuid4()), self.rejected_only(11))  # ordered 10
        self.assertEqual(r.status_code, 400)
        self.assertEqual(GoodsReceiptNote.query.count(), 0)

    def test_reject_then_receive_the_replacement_completes_the_order(self):
        self.assertEqual(self.post(str(uuid.uuid4()), self.rejected_only(2)).status_code, 200)
        self.assertEqual(self.post(str(uuid.uuid4()), self.body(8)).status_code, 200)
        self.assertEqual(self.stock(), 400.0)


if __name__ == '__main__':
    import unittest
    unittest.main()
