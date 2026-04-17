# Copyright 2020 Tecnativa - Ernesto Tejeda
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class RmaDeliveryWizardLine(models.TransientModel):
    _name = "rma.delivery.wizard.line"
    _description = "RMA Delivery Wizard Line"

    wizard_id = fields.Many2one(comodel_name="rma.delivery.wizard", ondelete="cascade")
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
    )
    qty = fields.Float(string="Quantity", required=True)
    product_uom = fields.Many2one(
        comodel_name="uom.uom",
        string="Unit of Measure",
        required=True,
    )

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.product_uom = self.product_id.uom_id


class RmaReDeliveryWizard(models.TransientModel):
    _name = "rma.delivery.wizard"
    _description = "RMA Delivery Wizard"

    rma_count = fields.Integer()
    type = fields.Selection(
        selection=[("replace", "Replace"), ("return", "Return to customer")],
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="rma.delivery.wizard.line",
        inverse_name="wizard_id",
        string="Products",
    )
    # kept for "return" type (single-line RMA)
    product_uom_qty = fields.Float(
        string="Product qty",
        digits="Product Unit of Measure",
    )
    product_uom = fields.Many2one(comodel_name="uom.uom", string="Unit of measure")
    scheduled_date = fields.Datetime(required=True, default=fields.Datetime.now)
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        required=True,
    )
    rma_return_grouping = fields.Boolean(
        string="Group RMA returns by customer address and warehouse",
        default=lambda self: self.env.company.rma_return_grouping,
    )

    @api.constrains("product_uom_qty")
    def _check_product_uom_qty(self):
        self.ensure_one()
        rma_ids = self.env.context.get("active_ids")
        if len(rma_ids) == 1 and self.product_uom_qty <= 0 and self.type == "return":
            raise ValidationError(_("Quantity must be greater than 0."))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        rma_ids = self.env.context.get("active_ids")
        rma = self.env["rma"].browse(rma_ids)
        warehouse_id = (
            self.env["stock.warehouse"]
            .search([("company_id", "=", rma[0].company_id.id)], limit=1)
            .id
        )
        delivery_type = self.env.context.get("rma_delivery_type")
        res.update(
            rma_count=len(rma),
            warehouse_id=warehouse_id,
            type=delivery_type,
        )
        if delivery_type == "replace" and len(rma) == 1:
            lines_by_product = {}
            for rma_line in rma.line_ids:
                if not rma_line.product_id:
                    continue
                key = rma_line.product_id.id
                if key not in lines_by_product:
                    lines_by_product[key] = {
                        "product_id": key,
                        "qty": 0.0,
                        "product_uom": rma_line.product_uom.id,
                    }
                lines_by_product[key]["qty"] += rma_line.qty
            res["line_ids"] = [(0, 0, vals) for vals in lines_by_product.values()]
        elif delivery_type == "return" and len(rma) == 1 and len(rma.line_ids) == 1:
            res.update(
                product_uom_qty=rma.remaining_qty if rma.remaining_qty > 0.0 else 0.0,
            )
        return res

    def action_deliver(self):
        self.ensure_one()
        rma_ids = self.env.context.get("active_ids")
        rma = self.env["rma"].browse(rma_ids)
        if self.type == "replace":
            rma.create_replace_from_wizard_lines(
                self.scheduled_date,
                self.warehouse_id,
                self.line_ids,
            )
        elif self.type == "return":
            qty = uom = None
            if self.rma_count == 1 and len(rma.line_ids) == 1:
                qty, uom = self.product_uom_qty, self.product_uom
            rma.with_context(
                rma_return_grouping=self.rma_return_grouping
            ).create_return(self.scheduled_date, qty, uom)
