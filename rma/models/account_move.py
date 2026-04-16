# Copyright 2020 Tecnativa - Ernesto Tejeda
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_rma_invoice_lines_qty(self):
        """We can't refund a different qty than the stated in the RMA.
        Extend to change criteria.

        For multi-line RMAs the check is done at the RMA level: the sum of all
        invoice lines linked to a given RMA must cover the total RMA quantity.
        For single-line RMAs the individual invoice line qty is checked directly.
        """
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        rma_totals = {}
        for line in self.sudo().mapped("invoice_line_ids").filtered("rma_id"):
            rma = line.rma_id
            rma_totals.setdefault(rma, 0.0)
            rma_totals[rma] += line.quantity
        return [
            rma
            for rma, total_qty in rma_totals.items()
            if float_compare(total_qty, rma.product_uom_qty, precision) < 0
        ]

    def action_post(self):
        """Avoids to validate a refund with less quantity of product than
        quantity in the linked RMA.
        """
        if self._check_rma_invoice_lines_qty():
            raise ValidationError(
                _(
                    "There is at least one invoice lines whose quantity is "
                    "less than the quantity specified in its linked RMA."
                )
            )
        return super().action_post()

    def unlink(self):
        rma = self.mapped("invoice_line_ids.rma_id")
        rma.write({"state": "received"})
        return super().unlink()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    rma_id = fields.Many2one(
        comodel_name="rma",
        string="RMA",
    )
