from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # RMAs that were created from a sale order
    rma_id = fields.Many2one(
        comodel_name="rma",
        inverse_name="order_id",
        string="RMAs",
        copy=False,
    )

    def show_rma(self):
        pass
