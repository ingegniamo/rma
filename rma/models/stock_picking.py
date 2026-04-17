# Copyright 2020 Tecnativa - Ernesto Tejeda
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    rma_count = fields.Integer(
        string="RMA count",
        compute="_compute_rma_count",
    )

    def _get_linked_rmas(self):
        return (
            self.move_ids.rma_ids
            | self.move_ids.rma_receiver_ids
            | self.move_ids.rma_id
            | self.move_ids.rma_line_id.rma_id
        )

    def _compute_rma_count(self):
        for rec in self:
            rec.rma_count = len(rec._get_linked_rmas())

    def copy(self, default=None):
        self.ensure_one()
        if self.env.context.get("set_rma_picking_type"):
            location_dest_id = default.get("location_dest_id")
            if location_dest_id:
                warehouse = self.env["stock.warehouse"].search(
                    [("rma_loc_id", "parent_of", location_dest_id)], limit=1
                )
                if warehouse:
                    default["picking_type_id"] = warehouse.rma_in_type_id.id
        return super().copy(default)

    def action_view_rma(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("rma.rma_action")
        rma = self._get_linked_rmas()
        if len(rma) == 1:
            action.update(
                res_id=rma.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        else:
            action["domain"] = [("id", "in", rma.ids)]
        return action
