# Copyright 2020 Tecnativa - Ernesto Tejeda
# Copyright 2023 Tecnativa - Pedro M. Baeza
# Copyright 2023 Michael Tietz (MT Software) <mtietz@mt-software.de>
# Copyright 2025 Tecnativa - Víctor Martínez
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging
import warnings
from collections import defaultdict
from itertools import groupby

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import html2plaintext

from odoo.addons.stock.models.stock_move import PROCUREMENT_PRIORITIES

_logger = logging.getLogger(__name__)


class Rma(models.Model):
    _name = "rma"
    _description = "RMA"
    _order = "date desc, priority"
    _inherit = ["mail.thread", "portal.mixin", "mail.activity.mixin"]

    def _domain_location_id(self):
        # this is done with sudo, intercompany rules are not applied by default so we
        # add company in domain explicitly to avoid multi-company rule error when
        # the user will try to choose a location
        rma_loc = (
            self.env["stock.warehouse"]
            .search([("company_id", "in", self.env.companies.ids)])
            .mapped("rma_loc_id")
        )
        return [("id", "child_of", rma_loc.ids)]

    # General fields
    sent = fields.Boolean()
    name = fields.Char(
        index=True,
        copy=False,
        default=lambda self: _("New"),
    )
    origin = fields.Char(
        string="Source Document",
        help="Reference of the document that generated this RMA.",
    )
    date = fields.Datetime(
        default=fields.Datetime.now,
        index=True,
        required=True,
    )
    deadline = fields.Date()
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsible",
        index=True,
        tracking=True,
    )
    team_id = fields.Many2one(
        comodel_name="rma.team",
        string="RMA team",
        index=True,
        compute="_compute_team_id",
        store=True,
    )
    tag_ids = fields.Many2many(comodel_name="rma.tag", string="Tags")
    finalization_id = fields.Many2one(
        string="Finalization Reason",
        comodel_name="rma.finalization",
        copy=False,
        domain=(
            "['|', ('company_id', '=', False), ('company_id', '='," " company_id)]"
        ),
        tracking=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        string="Customer",
        comodel_name="res.partner",
        index=True,
        tracking=True,
    )
    partner_shipping_id = fields.Many2one(
        string="Shipping Address",
        comodel_name="res.partner",
        help="Shipping address for current RMA.",
        compute="_compute_partner_shipping_id",
        store=True,
        readonly=False,
    )
    partner_invoice_id = fields.Many2one(
        string="Invoice Address",
        comodel_name="res.partner",
        domain=(
            "['|', ('company_id', '=', False), ('company_id', '='," " company_id)]"
        ),
        help="Refund address for current RMA.",
        compute="_compute_partner_invoice_id",
        store=True,
        readonly=False,
    )
    commercial_partner_id = fields.Many2one(
        comodel_name="res.partner",
        related="partner_id.commercial_partner_id",
    )
    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Origin Delivery",
        domain=(
            "["
            "    ('state', '=', 'done'),"
            "    ('picking_type_id.code', '=', 'outgoing'),"
            "    ('partner_id', 'child_of', commercial_partner_id),"
            "]"
        ),
    )
    move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Origin move",
        domain=(
            "["
            "    ('picking_id', '=', picking_id),"
            "    ('picking_id', '!=', False)"
            "]"
        ),
        compute="_compute_move_id",
        store=True,
        readonly=False,
    )
    # product_id = fields.Many2one(
    #     comodel_name="product.product",
    #     domain=[("type", "in", ["consu", "product"])],
    #     compute="_compute_product_id",
    #     store=True,
    #     readonly=False,
    # )
    product_uom_qty = fields.Float(
        string="Quantity",
        required=True,
        default=1.0,
        digits="Product Unit of Measure",
        compute="_compute_product_uom_qty",
        store=True,
        readonly=False,
    )
    # product_uom = fields.Many2one(
    #     comodel_name="uom.uom",
    #     string="UoM",
    #     required=True,
    #     default=lambda self: self.env.ref("uom.product_uom_unit").id,
    #     compute="_compute_product_uom",
    #     store=True,
    #     readonly=False,
    # )
    procurement_group_id = fields.Many2one(
        comodel_name="procurement.group",
        string="Procurement group",
    )
    priority = fields.Selection(
        selection=PROCUREMENT_PRIORITIES,
        default="1",
    )
    operation_id = fields.Many2one(
        comodel_name="rma.operation",
        string="Requested operation",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("received", "Received"),
            ("waiting_return", "Waiting for return"),
            ("waiting_replacement", "Waiting for replacement"),
            ("refunded", "Refunded"),
            ("returned", "Returned"),
            ("replaced", "Replaced"),
            ("finished", "Finished"),
            ("locked", "Locked"),
            ("cancelled", "Canceled"),
        ],
        default="draft",
        copy=False,
        tracking=True,
    )
    description = fields.Html()
    # Reception fields
    location_id = fields.Many2one(
        comodel_name="stock.location",
        domain=_domain_location_id,
        compute="_compute_location_id",
        store=True,
        readonly=False,
    )
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        compute="_compute_warehouse_id",
        store=True,
    )
    reception_move_id = fields.Many2one(
        comodel_name="stock.move",
        string="Reception move",
        copy=False,
    )
    # Refund fields
    refund_id = fields.Many2one(
        comodel_name="account.move",
        copy=False,
    )
    refund_line_id = fields.Many2one(
        comodel_name="account.move.line",
        copy=False,
    )
    can_be_refunded = fields.Boolean(compute="_compute_can_be_refunded")
    # Delivery fields
    delivery_move_ids = fields.One2many(
        comodel_name="stock.move",
        inverse_name="rma_id",
        string="Delivery reservation",
        copy=False,
    )
    delivery_picking_count = fields.Integer(
        string="Delivery count",
        compute="_compute_delivery_picking_count",
    )
    delivered_qty = fields.Float(
        digits="Product Unit of Measure",
        compute="_compute_delivered_qty",
        store=True,
    )
    can_be_returned = fields.Boolean(
        compute="_compute_can_be_returned",
    )
    can_be_replaced = fields.Boolean(
        compute="_compute_can_be_replaced",
    )
    can_be_locked = fields.Boolean(
        compute="_compute_can_be_locked",
    )
    can_be_finished = fields.Boolean(
        compute="_compute_can_be_finished",
    )
    remaining_qty = fields.Float(
        string="Remaining delivered qty",
        digits="Product Unit of Measure",
        compute="_compute_remaining_qty",
    )
    # uom_category_id = fields.Many2one(
    #     related="product_id.uom_id.category_id", string="Category UoM"
    # )
    # Split fields
    can_be_split = fields.Boolean(
        compute="_compute_can_be_split",
    )
    origin_split_rma_id = fields.Many2one(
        comodel_name="rma",
        string="Extracted from",
        copy=False,
    )

    show_create_receipt = fields.Boolean(
        string="Show Create Receipt Button", compute="_compute_show_create_receipt"
    )
    show_create_return = fields.Boolean(
        string="Show Create Return Button", compute="_compute_show_create_return"
    )
    show_create_replace = fields.Boolean(
        string="Show Create replace Button", compute="_compute_show_create_replace"
    )
    show_create_refund = fields.Boolean(
        string="Show Create refund Button", compute="_compute_show_refund_replace"
    )
    return_product_id = fields.Many2one(
        "product.product",
        help="Product to be returned if it's different from the originally delivered "
        "item.",
    )
    different_return_product = fields.Boolean(
        related="operation_id.different_return_product"
    )
    manual_finish_allowed = fields.Boolean(
        compute="_compute_manual_finish_allowed",
        help="Indicates whether this RMA can be manually finished, "
        "without requiring further processing such as a receipt, "
        "delivery, or refund.",
    )

    channel_id = fields.Char()

    channel = fields.Selection([
        ("shopify", "Shopify"),
        ("pepperi", "Pepperi"),
        ("commercehub", "CommerceHub")
    ],
        string="Canale"
    )
    note = fields.Text()

    rma_invoice_count = fields.Integer(
        string="Numero di fatture collegate all'RMA",
        compute="_compute_rma_invoice_count"
    )

    line_ids = fields.One2many(
        comodel_name='rma.line',
        inverse_name='rma_id',
        string='Line',
        copy=False
    )

    currency_id = fields.Many2one(
        comodel_name='res.currency',
        ondelete='restrict',
        string='Currency',
        readonly=True,
        copy=False
    )

    fiscal_position_id = fields.Many2one(
        comodel_name='account.fiscal.position',
        ondelete='set null',
        string='Fiscal Position',
        help="""Fiscal positions are used to adapt taxes and accounts for particular customers or sales orders/invoices.The default value comes from the customer.""",
        copy=False
    )

    move_ids = fields.Many2many(
        comodel_name='stock.move',
        relation='rel_move_rma_table',
        column1='rma_id',
        column2='move_id',
        string='Origin move',
        copy=False
    )

    reception_move_ids = fields.Many2many(
        comodel_name='stock.move',
        relation='rel_rma_stock_move_table',
        column1='rma_receiver_id',
        column2='reception_id',
        string='Reception move',
        copy=False
    )

    sale_order_count = fields.Integer(
        string='Sale Order Count',
        compute='_compute_sale_order_count',
    )

    sale_order_ids = fields.One2many(
        comodel_name='sale.order',
        inverse_name='rma_id',
        string='Sales Order',
    )

    show_update_fpos = fields.Boolean(
        string='Has Fiscal Position Changed',
        compute='_compute_show_update_fpos'
    )

    type_operation = fields.Selection(
        related='operation_id.type_operation',
        store=True
    )

    @api.depends('sale_order_ids')
    def _compute_sale_order_count(self):
        for rma_id in self:
            rma_id.sale_order_count = len(rma_id.sale_order_ids)

    @api.depends('fiscal_position_id')
    def _compute_show_update_fpos(self):
        for rma_id in self:
            rma_id.show_update_fpos = False

    def get_related_invoice_ids(self):
        invoice_list = []
        # TODO: Collect all invoices
        # for ddt in self.reception_move_ids.ddt_ids:
        #     inv_ddt = ddt.get_invoices()
        #     if inv_ddt:
        #         invoice_list +=inv_ddt
        return invoice_list

    def _compute_rma_invoice_count(self):
        for rma in self:
            rma.rma_invoice_count = len(rma.get_related_invoice_ids())

    @api.depends("operation_id", "reception_move_ids.state", "reception_move_id.state")
    def _compute_manual_finish_allowed(self):
        """
        compute whether the RMA requires any follow-up action based on the
        operation configuration
        """
        for rma in self:
            reception_moves = rma.reception_move_ids or rma.reception_move_id
            reception_pending = bool(
                rma.operation_id.action_create_receipt
                and any(m.state != "done" for m in reception_moves)
            )
            rma.manual_finish_allowed = (
                reception_pending
                or rma.operation_id.action_create_delivery
                or rma.operation_id.action_create_refund
            )

    @api.depends(
        "operation_id.action_create_receipt", "state",
        "reception_move_id", "reception_move_ids",
    )
    def _compute_show_create_receipt(self):
        for rec in self:
            rec.show_create_receipt = (
                not rec.reception_move_ids
                and not rec.reception_move_id
                and rec.operation_id.action_create_receipt == "manual_on_confirm"
                and rec.state == "confirmed"
            )

    @api.depends("operation_id.action_create_delivery", "can_be_returned")
    def _compute_show_create_return(self):
        for rec in self:
            rec.show_create_return = (
                rec.operation_id.action_create_delivery
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_returned
            )

    @api.depends("operation_id.action_create_delivery", "can_be_replaced")
    def _compute_show_create_replace(self):
        for rec in self:
            rec.show_create_replace = (
                rec.operation_id.action_create_delivery
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_replaced
            )

    @api.depends("operation_id.action_create_refund", "can_be_refunded")
    def _compute_show_refund_replace(self):
        for rec in self:
            rec.show_create_refund = (
                rec.operation_id.action_create_refund
                in ("manual_on_confirm", "manual_after_receipt")
                and rec.can_be_refunded
            )

    def _compute_delivery_picking_count(self):
        for rma in self:
            rma.delivery_picking_count = len(rma.delivery_move_ids.picking_id)

    @api.depends(
        "delivery_move_ids.state",
        "delivery_move_ids.scrapped",
        "delivery_move_ids.quantity",
    )
    def _compute_delivered_qty(self):
        """Sum done quantities across all delivery moves for this RMA.

        Only counts moves in state 'done' so that remaining_qty correctly
        reflects what has actually been sent/returned, not just what is
        reserved.  For multi-line RMAs quantities are summed as raw numbers
        (no UoM conversion) — the result drives remaining_qty > 0 checks
        in the state machine.
        """
        for record in self:
            delivered = 0.0
            for move in record.delivery_move_ids.filtered(
                lambda m: m.state == "done" and not m.scrapped
            ):
                delivered += move.quantity
            record.delivered_qty = delivered

    @api.depends("product_uom_qty", "delivered_qty")
    def _compute_remaining_qty(self):
        """Compute 'remaining_qty' field.

        remaining_qty: is used to set a default quantity of replacing
        or returning of product to the customer.
        """
        for r in self:
            r.remaining_qty = r.product_uom_qty - r.delivered_qty

    @api.depends(
        "state",
    )
    def _compute_can_be_refunded(self):
        """Compute 'can_be_refunded'. This field controls the visibility
        of 'Refund' button in the rma form view and determinates if
        an rma can be refunded. It is used in rma.action_refund method.
        """
        for record in self:
            record.can_be_refunded = (
                record.operation_id.action_create_refund
                in ("manual_after_receipt", "automatic_after_receipt")
                and record.state == "received"
            ) or (
                record.operation_id.action_create_refund
                in ("manual_on_confirm", "automatic_on_confirm")
                and record.state == "confirmed"
            )

    @api.depends("remaining_qty", "state", "operation_id.action_create_delivery")
    def _compute_can_be_returned(self):
        """Compute 'can_be_returned'. This field controls the visibility
        of the 'Return to customer' button in the rma form
        view and determinates if an rma can be returned to the customer.
        This field is used in:
        rma._compute_can_be_split
        rma._ensure_can_be_returned.
        """
        for r in self:
            r.can_be_returned = r.remaining_qty > 0 and (
                (
                    r.operation_id.action_create_delivery
                    in ("manual_after_receipt", "automatic_after_receipt")
                    and r.state in ["received", "waiting_return"]
                )
                or (
                    r.operation_id.action_create_delivery
                    in ("manual_on_confirm", "automatic_on_confirm")
                    and r.state == "confirmed"
                )
            )

    @api.depends("state")
    def _compute_can_be_replaced(self):
        """Compute 'can_be_replaced'. This field controls the visibility
        of 'Replace' button in the rma form
        view and determinates if an rma can be replaced.
        This field is used in:
        rma._compute_can_be_split
        rma._ensure_can_be_replaced.
        """
        for r in self:
            r.can_be_replaced = (
                r.operation_id.action_create_delivery
                in ("manual_after_receipt", "automatic_after_receipt")
                and r.state
                in [
                    "received",
                    "waiting_replacement",
                    "replaced",
                ]
            ) or (
                r.operation_id.action_create_delivery
                in ("manual_on_confirm", "automatic_on_confirm")
                and r.state == "confirmed"
            )

    @api.depends("state", "remaining_qty", "manual_finish_allowed")
    def _compute_can_be_finished(self):
        # The RMA can be finished if:
        # - It's in a transitional state AND there is still quantity to process
        # OR
        # - It's not already finished AND no further action is required
        for rma in self:
            rma.can_be_finished = (
                rma.state in {"received", "waiting_replacement", "waiting_return"}
                and rma.remaining_qty > 0
            ) or (rma.state != "finished" and not rma.manual_finish_allowed)

    @api.depends("product_uom_qty", "state", "remaining_qty")
    def _compute_can_be_split(self):
        """Compute 'can_be_split'. This field controls the
        visibility of 'Split' button in the rma form view and
        determinates if an rma can be split.
        This field is used in:
        rma._ensure_can_be_split
        """
        for r in self:
            # Split only makes sense for single-line RMAs (one product, qty > 1).
            # For multi-line RMAs each product is already its own line.
            if (
                len(r.line_ids) <= 1
                and r.product_uom_qty > 1
                and (
                    (r.state == "waiting_return" and r.remaining_qty > 0)
                    or (r.state == "waiting_replacement" and r.remaining_qty > 0)
                )
            ):
                r.can_be_split = True
            else:
                r.can_be_split = False

    @api.depends("state")
    def _compute_can_be_locked(self):
        for r in self:
            r.can_be_locked = r.remaining_qty > 0 and r.state in [
                "received",
                "waiting_return",
                "waiting_replacement",
            ]

    @api.depends("location_id")
    def _compute_warehouse_id(self):
        for record in self.filtered("location_id"):
            record.warehouse_id = self.env["stock.warehouse"].search(
                [("rma_loc_id", "parent_of", record.location_id.id)], limit=1
            )

    @api.depends("user_id")
    def _compute_team_id(self):
        self.team_id = False
        for record in self.filtered("user_id"):
            record.team_id = (
                self.env["rma.team"]
                .sudo()
                .search(
                    [
                        "|",
                        ("user_id", "=", record.user_id.id),
                        ("member_ids", "=", record.user_id.id),
                        "|",
                        ("company_id", "=", False),
                        ("company_id", "child_of", record.company_id.ids),
                    ],
                    limit=1,
                )
            )

    @api.depends("partner_id")
    def _compute_partner_shipping_id(self):
        self.partner_shipping_id = False
        for record in self.filtered("partner_id"):
            address = record.partner_id.address_get(["delivery"])
            record.partner_shipping_id = address.get("delivery", False)

    @api.depends("partner_id")
    def _compute_partner_invoice_id(self):
        self.partner_invoice_id = False
        for record in self.filtered("partner_id"):
            address = record.partner_id.address_get(["invoice"])
            record.partner_invoice_id = address.get("invoice", False)

    @api.depends("picking_id")
    def _compute_move_id(self):
        """Empty move on picking change, but selecting the move in it if it's single."""
        self.move_id = False
        for record in self.filtered("picking_id"):
            if len(record.picking_id.move_ids) == 1:
                record.move_id = record.picking_id.move_ids.id

    # @api.depends("move_id")
    # def _compute_product_id(self):
    #     self.product_id = False
    #     for record in self.filtered("move_id"):
    #         record.product_id = record.move_id.product_id.id

    @api.depends("line_ids.qty", "move_id")
    def _compute_product_uom_qty(self):
        for record in self:
            if record.line_ids:
                record.product_uom_qty = sum(record.line_ids.mapped("qty"))
            elif record.move_id:
                record.product_uom_qty = record.move_id.product_uom_qty
            else:
                record.product_uom_qty = 0.0

    # @api.depends("move_id", "product_id")
    # def _compute_product_uom(self):
    #     for record in self:
    #         if record.move_id:
    #             record.product_uom = record.move_id.product_uom.id
    #         elif record.product_id:
    #             record.product_uom = record.product_id.uom_id
    #         else:
    #             record.product_uom = False

    @api.depends(
        "picking_id",
        # "product_id",
        "company_id"
    )
    def _compute_location_id(self):
        for record in self:
            if record.picking_id:
                warehouse = record.picking_id.picking_type_id.warehouse_id
                record.location_id = warehouse.rma_loc_id.id
            elif not record.location_id:
                company = record.company_id or self.env.company
                warehouse = self.env["stock.warehouse"].search(
                    [("company_id", "=", company.id)], limit=1
                )
                record.location_id = warehouse.rma_loc_id.id

    def _compute_access_url(self):
        for record in self:
            record.access_url = f"/my/rmas/{record.id}"

    # Constrains methods (@api.constrains)
    @api.constrains(
        "state",
        "partner_id",
        "partner_shipping_id",
        "partner_invoice_id",
        #"product_id",
    )
    def _check_required_after_draft(self):
        """Check that RMAs are being created or edited with the
        necessary fields filled out. Only applies to 'Draft' and
        'Cancelled' states.
        """
        rma = self.filtered(lambda r: r.state not in ["draft", "cancelled"])
        rma._ensure_required_fields()

    # CRUD methods (ORM overrides)
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                ir_sequence = self.env["ir.sequence"]
                if "company_id" in vals:
                    ir_sequence = ir_sequence.with_company(vals["company_id"])
                vals["name"] = ir_sequence.next_by_code("rma")
            # Assign a default team_id which will be the first in the sequence
            if not vals.get("team_id"):
                vals["team_id"] = self.env["rma.team"].search([], limit=1).id
        rmas = super().create(vals_list)
        # Send acknowledge when the RMA is created from the portal and the
        # company has the proper setting active. This context is set by the
        # `rma_sale` module.
        if self.env.context.get("from_portal"):
            rmas._send_draft_email()
        return rmas

    def copy(self, default=None):
        team = super().copy(default)
        for follower in self.message_follower_ids:
            team.message_subscribe(
                partner_ids=follower.partner_id.ids,
                subtype_ids=follower.subtype_ids.ids,
            )
        return team

    def unlink(self):
        if self.filtered(lambda r: r.state != "draft"):
            raise ValidationError(
                _("You cannot delete RMAs that are not in draft state")
            )
        return super().unlink()

    def _send_draft_email(self):
        """Send customer notifications they place the RMA from the portal"""
        for rma in self.filtered("company_id.send_rma_draft_confirmation"):
            rma.with_context(
                force_send=True,
                mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_draft_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_draft_confirmation_template_id.id
                ],
                subtype_xmlid="rma.mt_rma_notification",
            )

    def _send_confirmation_email(self):
        """Auto send notifications"""
        for rma in self.filtered(lambda p: p.company_id.send_rma_confirmation):
            rma.with_context(
                force_send=True,
                mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_confirmation_template_id.id
                ],
                subtype_xmlid="rma.mt_rma_notification",
            )

    def _send_receipt_confirmation_email(self):
        """Send customer notifications when the products are received"""
        for rma in self.filtered("company_id.send_rma_receipt_confirmation"):
            rma.with_context(
                force_send=True,
                mark_rma_as_sent=True,
            ).message_post_with_source(
                rma.company_id.rma_mail_receipt_confirmation_template_id.get_external_id()[
                    rma.company_id.rma_mail_receipt_confirmation_template_id.id
                ],
                subtype_xmlid="rma.mt_rma_notification",
            )

    # Action methods
    def action_rma_send(self):
        self.ensure_one()
        template = self.env.ref("rma.mail_template_rma_notification", False)
        template = self.company_id.rma_mail_confirmation_template_id or template
        form = self.env.ref("mail.email_compose_message_wizard_form", False)
        ctx = {
            "default_model": "rma",
            "default_subtype_id": self.env.ref("rma.mt_rma_notification").id,
            "default_res_ids": self.ids,
            "default_use_template": bool(template),
            "default_template_id": template and template.id or False,
            "default_composition_mode": "comment",
            "mark_rma_as_sent": True,
            "model_description": "RMA",
            "force_email": True,
        }
        return {
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(form.id, "form")],
            "view_id": form.id,
            "target": "new",
            "context": ctx,
        }

    def _add_message_subscribe_partner(self):
        self.ensure_one()
        if self.partner_id and self.partner_id not in self.message_partner_ids:
            self.message_subscribe([self.partner_id.id])

    def _prepare_procurement_group_vals(self):
        return {
            "move_type": "direct",
            "partner_id": self and self.partner_shipping_id.id or False,
            "name": self and ", ".join(self.mapped("name")) or False,
        }

    def _prepare_common_procurement_vals(
        self, warehouse=None, scheduled_date=None, group=None
    ):
        self.ensure_one()
        group = group or self.procurement_group_id
        if not group:
            group = self.env["procurement.group"].create(
                self._prepare_procurement_group_vals()
            )
        return {
            "company_id": self.company_id,
            "group_id": group,
            "date_planned": scheduled_date or fields.Datetime.now(),
            "warehouse_id": warehouse or self.warehouse_id,
            "partner_id": group.partner_id.id,
            "priority": self.priority,
        }

    def _prepare_reception_procurement_vals(self, group=None):
        """This method is used only for reception and a specific RMA IN route."""
        vals = self._prepare_common_procurement_vals(group=group)
        vals["route_ids"] = self.warehouse_id.rma_in_route_id
        vals["rma_receiver_ids"] = [(6, 0, self.ids)]
        vals["to_refund"] = self.operation_id.action_create_refund == "update_quantity"
        if self.move_id:
            vals["origin_returned_move_id"] = self.move_id.id
            if not self.operation_id.different_return_product:
                vals["move_orig_ids"] = [(6, 0, self.move_id.ids)]
        return vals

    def _prepare_reception_procurements(self):
        procurements = []
        group_model = self.env["procurement.group"]
        for rma in self:
            group = rma.procurement_group_id
            if not group:
                group = group_model.create(rma._prepare_procurement_group_vals())
            for rma_line in rma.line_ids:
                if not rma_line._product_is_storable():
                    continue
                product = rma_line.product_id
                vals = rma._prepare_reception_procurement_vals(group)
                vals["price_unit"] = rma_line.price_unit
                vals["rma_line_id"] = rma_line.id
                vals["tax_ids"] = [(6, 0, rma_line.tax_ids.ids)]
                vals["account_line_id"] = rma_line.account_line_id.id
                procurements.append(
                    group_model.Procurement(
                        product,
                        rma_line.qty,
                        rma_line.product_uom,
                        rma.location_id,
                        product.display_name,
                        group.name,
                        rma.company_id,
                        vals,
                    )
                )
        return procurements

    def _create_receipt(self):
        self.ensure_one()
        procurements = self._prepare_reception_procurements()
        if procurements:
            self.env["procurement.group"].run(procurements)
        # Collect all reception moves created via the procurement group and
        # populate the Many2many so the rest of the code can find them all.
        reception_moves = self.env["stock.move"].search([
            ("group_id", "=", self.procurement_group_id.id),
            ("location_dest_id", "child_of", self.location_id.id),
            ("state", "not in", ["done", "cancel"]),
        ])
        if reception_moves:
            self.reception_move_ids = [(4, m.id) for m in reception_moves]
        # Assign reservation and optionally auto-confirm
        self.reception_move_ids.picking_id.action_assign()
        if self.operation_id.auto_confirm_reception:
            self.reception_move_ids.write({"picked": True})
            self.reception_move_ids._action_done()

    def action_create_receipt(self):
        self.ensure_one()
        self._create_receipt()
        self.ensure_one()
        picking = (
            self.reception_move_ids[:1].picking_id
            or self.reception_move_id.picking_id
        )
        return {
            "name": _("Receipt"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "stock.picking",
            "views": [[False, "form"]],
            "res_id": picking.id,
        }

    def action_confirm(self):
        """Invoked when 'Confirm' button in rma form view is clicked."""
        self._ensure_required_fields()
        self = self.filtered(lambda rma: rma.state == "draft")
        if not self:
            return
        self._assign_reception_procurement_group()
        self.write({"state": "confirmed"})
        for rma in self:
            rma._add_message_subscribe_partner()
        self._send_confirmation_email()
        for rec in self:
            if rec.operation_id.action_create_receipt == "automatic_on_confirm":
                rec._create_receipt()
            if rec.operation_id.action_create_delivery == "automatic_on_confirm":
                rec.with_context(
                    rma_return_grouping=rec.env.company.rma_return_grouping
                ).create_replace(
                    fields.Datetime.now(),
                    rec.warehouse_id,
                    False,
                    0.0,
                    False,
                )
            if rec.operation_id.action_create_refund == "automatic_on_confirm":
                rec.action_refund()

    def action_refund(self):
        """Invoked when 'Refund' button in rma form view is clicked
        and 'rma_refund_action_server' server action is run.
        """
        group_dict = {}
        for record in self.filtered("can_be_refunded"):
            key = (record.partner_invoice_id.id, record.company_id.id)
            group_dict.setdefault(key, self.env["rma"])
            group_dict[key] |= record
        for rmas in group_dict.values():
            origin = ", ".join(rmas.mapped("name"))
            refund_vals = rmas[0]._prepare_refund_vals(origin)
            for rma in rmas:
                for rma_line in rma.line_ids:
                    refund_vals["invoice_line_ids"].append(
                        (0, 0, rma._prepare_refund_line_vals(rma_line))
                    )
            refund = self.env["account.move"].sudo().create(refund_vals)
            refund.with_user(self.env.uid).message_post_with_source(
                "mail.message_origin_link",
                render_values={"self": refund, "origin": rmas},
                subtype_id=self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_note"),
            )
            for line in refund.invoice_line_ids:
                line.rma_id.write(
                    {
                        "refund_line_id": line.id,
                        "refund_id": refund.id,
                        "state": "refunded",
                    }
                )

    def action_replace(self):
        """Invoked when 'Replace' button in rma form view is clicked."""
        self.ensure_one()
        self._ensure_can_be_replaced()
        # Force active_id to avoid issues when coming from smart buttons
        # in other models
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma.rma_delivery_wizard_action"
        )
        action["name"] = "Replace product(s)"
        action["context"] = dict(self.env.context)
        action["context"].update(
            active_id=self.id,
            active_ids=self.ids,
            rma_delivery_type="replace",
        )
        return action

    def action_return(self):
        """Invoked when 'Return to customer' button in rma form
        view is clicked.
        """
        self.ensure_one()
        self._ensure_can_be_returned()
        # Force active_id to avoid issues when coming from smart buttons
        # in other models
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma.rma_delivery_wizard_action"
        )
        action["context"] = dict(self.env.context)
        action["context"].update(
            active_id=self.id,
            active_ids=self.ids,
            rma_delivery_type="return",
        )
        return action

    def action_split(self):
        """Invoked when 'Split' button in rma form view is clicked."""
        self.ensure_one()
        self._ensure_can_be_split()
        # Force active_id to avoid issues when coming from smart buttons
        # in other models
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma.rma_split_wizard_action"
        )
        action["context"] = dict(self.env.context)
        action["context"].update(active_id=self.id, active_ids=self.ids)
        return action

    def action_finish(self):
        """Invoked when a user wants to manually finalize the RMA"""
        self.ensure_one()
        if not self.manual_finish_allowed:
            self.state = "finished"
            return {}
        all_reception_moves = self.reception_move_ids | self.reception_move_id
        if (
            self.operation_id.action_create_receipt
            and any(m.state != "done" for m in all_reception_moves)
        ):
            raise ValidationError(
                _("The reception must be done before finishing this rma")
            )
        self._ensure_can_be_returned()
        # Force active_id to avoid issues when coming from smart buttons
        # in other models
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rma.rma_finalization_wizard_action"
        )
        action["context"] = dict(self.env.context)
        action["context"].update(active_id=self.id, active_ids=self.ids)
        return action

    def action_cancel(self):
        """Invoked when 'Cancel' button in rma form view is clicked."""
        (self.reception_move_ids | self.reception_move_id)._action_cancel()
        self.write({
            "state": "cancelled",
            "reception_move_ids": [(5, 0, 0)],
            "reception_move_id": False,
        })

    def action_draft(self):
        cancelled_rma = self.filtered(lambda r: r.state == "cancelled")
        cancelled_rma.write({"state": "draft"})

    def action_lock(self):
        """Invoked when 'Lock' button in rma form view is clicked."""
        self.filtered("can_be_locked").write({"state": "locked"})

    def action_unlock(self):
        """Invoked when 'Unlock' button in rma form view is clicked."""
        locked_rma = self.filtered(lambda r: r.state == "locked")
        locked_rma.write({"state": "received"})

    def action_preview(self):
        """Invoked when 'Preview' button in rma form view is clicked."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "target": "self",
            "url": self.get_portal_url(),
        }

    def _action_view_pickings(self, pickings):
        self.ensure_one()
        # Force active_id to avoid issues when coming from smart buttons
        # in other models
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        if len(pickings) > 1:
            action["domain"] = [("id", "in", pickings.ids)]
        elif pickings:
            action.update(
                res_id=pickings.id,
                view_mode="form",
                view_id=False,
                views=False,
            )
        return action

    def action_view_receipt(self):
        """Invoked when 'Receipt' smart button in rma form view is clicked."""
        pickings = self.mapped("reception_move_ids.picking_id") | self.mapped(
            "reception_move_id.picking_id"
        )
        return self._action_view_pickings(pickings)

    def action_view_refund(self):
        """Invoked when 'Refund' smart button in rma form view is clicked."""
        self.ensure_one()
        return {
            "name": _("Refund"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "account.move",
            "views": [(self.env.ref("account.view_move_form").id, "form")],
            "res_id": self.refund_id.id,
        }

    def action_view_delivery(self):
        """Invoked when 'Delivery' smart button in rma form view is clicked."""
        return self._action_view_pickings(self.mapped("delivery_move_ids.picking_id"))

    # Validation business methods
    def _ensure_required_fields(self):
        """This method is used to ensure the following fields are not empty:
        [
            'partner_id', 'partner_invoice_id', 'partner_shipping_id',
            'product_id', 'location_id'
        ]

        This method is intended to be called on confirm RMA action and is
        invoked by:
        rma._check_required_after_draft
        rma.action_confirm
        """
        required = [
            "partner_id",
            "partner_shipping_id",
            "partner_invoice_id",
            #"product_id",
            "location_id",
            "operation_id",
        ]
        for record in self:
            if not record.line_ids:
                raise ValidationError(
                    _("At least one product line is required before confirming the RMA.")
                )
            desc = ""
            for field in filter(lambda item: not record[item], required):
                field_record = (
                    self.env["ir.model.fields"]
                    .sudo()
                    .search(
                        [
                            ("model_id.model", "=", record._name),
                            ("name", "=", field),
                        ]
                    )
                )
                desc += f"\n{field_record.field_description}"
            if desc:
                raise ValidationError(_("Required field(s):%s") % desc)

    def _ensure_can_be_returned(self):
        """This method is intended to be invoked after user click on
        'Replace' or 'Return to customer' button (before the delivery wizard
        is launched) and after confirm the wizard.

        This method is invoked by:
        rma.action_replace
        rma.action_return
        rma.create_replace
        rma.create_return
        """
        if len(self) == 1:
            if not self.can_be_returned:
                raise ValidationError(_("This RMA cannot perform a return."))
        elif not self.filtered("can_be_returned"):
            raise ValidationError(_("None of the selected RMAs can perform a return."))

    def _ensure_can_be_replaced(self):
        """This method is intended to be invoked after user click on
        'Replace' button (before the delivery wizard
        is launched) and after confirm the wizard.

        This method is invoked by:
        rma.action_replace
        rma.create_replace
        """
        if len(self) == 1:
            if not self.can_be_replaced:
                raise ValidationError(_("This RMA cannot perform a replacement."))
        elif not self.filtered("can_be_replaced"):
            raise ValidationError(
                _("None of the selected RMAs can perform a replacement.")
            )

    def _ensure_can_be_split(self):
        """intended to be called before launch and after save the split wizard.
        invoked by:
        rma.action_split
        rma.extract_quantity
        """
        self.ensure_one()
        if not self.can_be_split:
            raise ValidationError(_("This RMA cannot be split."))

    def _ensure_qty_to_return(self, qty=None, uom=None):
        """This method is intended to be invoked after confirm the wizard.
        invoked by: rma.create_return
        """
        # TODO: Check _ensure_qty_to_return
        # if qty and uom:
        #     if uom != self.product_uom:
        #         qty = uom._compute_quantity(qty, self.product_uom)
        #     if qty > self.remaining_qty:
        #         raise ValidationError(
        #             _("The quantity to return is greater than " "remaining quantity.")
        #         )

    def _ensure_qty_to_extract(self, qty, uom):
        """This method is intended to be invoked after confirm the wizard.
        invoked by: rma.extract_quantity
        """
        # TODO: _ensure_qty_to_extract
        # to_split_uom_qty = qty
        # if uom != self.product_uom:
        #     to_split_uom_qty = uom._compute_quantity(qty, self.product_uom)
        # if to_split_uom_qty > self.remaining_qty:
        #     raise ValidationError(
        #         _(
        #             "Quantity to extract cannot be greater than remaining"
        #             " delivery quantity (%(remaining_qty)s %(product_uom)s)"
        #         )
        #         % (
        #             {
        #                 "remaining_qty": self.remaining_qty,
        #                 "product_uom": self.product_uom.name,
        #             }
        #         )
        #     )

    # Extract business methods
    def extract_quantity(self, qty, uom):
        self.ensure_one()
        self._ensure_can_be_split()
        self._ensure_qty_to_extract(qty, uom)
        self.product_uom_qty -= qty
        if self.remaining_qty <= 0:
            if self.state == "waiting_return":
                self.state = "returned"
            elif self.state == "waiting_replacement":
                self.state = "replaced"
        # TODO: Check if we need to copy the lines
        extracted_rma = self.copy(
            {
                "origin": self.name,
                "product_uom_qty": qty,
#                "product_uom": uom.id,
                "state": "received",
                "reception_move_id": self.reception_move_id.id,
                "origin_split_rma_id": self.id,
            }
        )
        extracted_rma.message_post_with_source(
            "mail.message_origin_link",
            render_values={"self": extracted_rma, "origin": self},
            subtype_id=self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_note"),
        )
        self.message_post(
            body=Markup(
                _(
                    'Split: <a href="#" data-oe-model="rma" '
                    'data-oe-id="%(id)d">%(name)s</a> has been created.'
                )
                % ({"id": extracted_rma.id, "name": extracted_rma.name})
            )
        )
        return extracted_rma

    # Refund business methods
    def _prepare_refund_vals(self, origin=False):
        """Hook method for preparing the refund Form.

        This method could be override in order to add new custom field
        values in the refund creation.

        invoked by:
        rma.action_refund
        """
        self.ensure_one()
        return {
            "move_type": "out_refund",
            "company_id": self.company_id.id,
            "partner_id": self.partner_invoice_id.id,
            "invoice_payment_term_id": False,
            "invoice_origin": origin,
            "invoice_line_ids": [],
        }

    def _prepare_refund_line_vals(self, rma_line):
        """Hook method for preparing a refund line.

        Takes a single rma.line record and returns the vals dict for one
        account.move.line on the credit note.

        invoked by:
        rma.action_refund
        """
        self.ensure_one()
        vals = {
            "product_id": rma_line.product_id.id,
            "quantity": rma_line.qty,
            "rma_id": self.id,
        }
        if rma_line.product_uom:
            vals["product_uom_id"] = rma_line.product_uom.id
        if rma_line.price_unit:
            vals["price_unit"] = rma_line.price_unit
        if rma_line.discount:
            vals["discount"] = rma_line.discount
        if rma_line.tax_ids:
            vals["tax_ids"] = [(6, 0, rma_line.tax_ids.ids)]
        return vals

    def _delivery_should_be_grouped(self):
        """Checks if the rmas should be grouped for the delivery process"""
        if any(self.operation_id.mapped("prevent_delivery_grouping")):
            return False
        if "rma_return_grouping" in self.env.context:
            return bool(self.env.context.get("rma_return_grouping"))
        return self.env.company.rma_return_grouping

    def _get_delivery_group_key(self):
        """Returns a key by which the rmas should be grouped for the delivery process"""
        self.ensure_one()
        return (self.partner_shipping_id.id, self.company_id.id, self.warehouse_id.id)

    def _get_reception_group_key(self):
        self.ensure_one()
        return (self.partner_id.id, self.company_id.id, self.warehouse_id.id)

    def _assign_reception_procurement_group(self):
        """Groups the given rmas by the returned key from _get_reception_group_key
        by setting the procurement_group_id on the each rma if there is not yet on
         set"""
        grouped_rmas = groupby(
            sorted(self, key=lambda rma: rma._get_reception_group_key()),
            key=lambda rma: [rma._get_reception_group_key()],
        )
        for _group, rmas in grouped_rmas:
            rmas = self.browse().concat(*list(rmas))
            if not rmas:
                continue
            proc_group = self.env["procurement.group"].create(
                rmas._prepare_procurement_group_vals()
            )
            rmas.write({"procurement_group_id": proc_group.id})

    def _assign_delivery_procurement_group(self):
        """Groups the given rmas by the returned key from _get_delivery_group_key
        by setting the procurement_group_id on the each rma if there is not yet on
        set"""
        if not self._delivery_should_be_grouped():
            return
        grouped_rmas = groupby(
            sorted(self, key=lambda rma: rma._get_delivery_group_key()),
            key=lambda rma: [rma._get_delivery_group_key()],
        )
        for _group, rmas in grouped_rmas:
            rmas = self.browse().concat(*list(rmas))
            if not rmas:
                continue
            proc_group = self.env["procurement.group"].create(
                rmas._prepare_procurement_group_vals()
            )
            rmas.write({"procurement_group_id": proc_group.id})

    def _prepare_delivery_procurement_vals(self, scheduled_date=None):
        """This method is used only for Delivery (not replace). It is important to set
        RMA Out route."""
        vals = self._prepare_common_procurement_vals(scheduled_date=scheduled_date)
        vals["rma_id"] = self.id
        vals["route_ids"] = self.warehouse_id.rma_out_route_id
        all_recv_moves = self.reception_move_ids | self.reception_move_id
        vals["move_orig_ids"] = [(6, 0, all_recv_moves.ids)]
        return vals

    def _prepare_delivery_procurements(self, scheduled_date=None, qty=None, uom=None):
        self._assign_delivery_procurement_group()
        procurements = []
        group_model = self.env["procurement.group"]
        for rma in self:
            if not rma.procurement_group_id:
                rma.procurement_group_id = group_model.create(
                    rma._prepare_procurement_group_vals()
                )

            vals = rma._prepare_delivery_procurement_vals(scheduled_date)
            group = vals.get("group_id")
            for rma_line in rma.line_ids:
                procurements.append(
                    group_model.Procurement(
                        rma_line.product_id,
                        qty or rma_line.qty,
                        uom or rma_line.product_uom,
                        rma.partner_shipping_id.property_stock_customer,
                        rma_line.product_id.display_name,
                        group.name,
                        rma.company_id,
                        vals,
                    )
                )
        return procurements

    # TODO: Replace this with a better implementation considering that we have multiple products
    def _product_is_storable(self, product=None):
        if product:
            return product.type in ["product", "consu"]

        self.ensure_one()
        return any([line._product_is_storable() for line in self.line_ids])

    # Returning business methods
    def create_return(self, scheduled_date, qty=None, uom=None):
        """Intended to be invoked by the delivery wizard"""
        self._ensure_can_be_returned()
        self._ensure_qty_to_return(qty, uom)
        rmas_to_return = self.filtered(
            lambda rma: rma.can_be_returned and rma._product_is_storable()
        )
        procurements = rmas_to_return._prepare_delivery_procurements(
            scheduled_date, qty, uom
        )
        if procurements:
            self.env["procurement.group"].run(procurements)
        pickings = defaultdict(lambda: self.browse())
        for rma in rmas_to_return:
            picking = rma.delivery_move_ids.picking_id.sorted("id", reverse=True)[0]
            pickings[picking] |= rma
            rma.message_post(
                body=Markup(
                    _(
                        'Return: <a href="#" data-oe-model="stock.picking" '
                        'data-oe-id="%(id)d">%(name)s</a> has been created.'
                    )
                    % ({"id": picking.id, "name": picking.name})
                )
            )
        for picking, rmas in pickings.items():
            picking.action_confirm()
            picking.action_assign()
            picking.message_post_with_source(
                "mail.message_origin_link",
                render_values={"self": picking, "origin": rmas},
                subtype_id=self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_note"),
            )
        rmas_to_return.write({"state": "waiting_return"})

    def _prepare_replace_procurement_vals(self, warehouse=None, scheduled_date=None):
        """This method is used only for Replace (not Delivery)."""
        vals = self._prepare_common_procurement_vals(warehouse, scheduled_date)
        vals["rma_id"] = self.id
        if self.warehouse_id.rma_out_replace_route_id:
            vals["route_ids"] = self.warehouse_id.rma_out_replace_route_id
        return vals

    def _prepare_replace_procurements(
        self, warehouse, scheduled_date, product, qty, uom
    ):
        procurements = []
        group_model = self.env["procurement.group"]
        for rma in self:
            if not rma.procurement_group_id:
                rma.procurement_group_id = group_model.create(
                    rma._prepare_procurement_group_vals()
                )

            vals = rma._prepare_replace_procurement_vals(warehouse, scheduled_date)
            group = vals.get("group_id")

            if product:
                # Explicit replacement product (e.g. different item than originally sent)
                if not rma._product_is_storable(product):
                    continue
                procurements.append(
                    group_model.Procurement(
                        product,
                        qty,
                        uom,
                        rma.partner_shipping_id.property_stock_customer,
                        product.display_name,
                        group.name,
                        rma.company_id,
                        vals,
                    )
                )
            else:
                # No override: send back one move per line (same product/qty as received)
                for rma_line in rma.line_ids:
                    if not rma_line._product_is_storable():
                        continue
                    procurements.append(
                        group_model.Procurement(
                            rma_line.product_id,
                            qty or rma_line.qty,
                            uom or rma_line.product_uom,
                            rma.partner_shipping_id.property_stock_customer,
                            rma_line.product_id.display_name,
                            group.name,
                            rma.company_id,
                            vals,
                        )
                    )
        return procurements

    # Replacing business methods
    def create_replace_from_wizard_lines(self, scheduled_date, warehouse, wizard_lines):
        """Invoked by the delivery wizard when replacing with multiple lines."""
        self.ensure_one()
        self._ensure_can_be_replaced()
        moves_before = self.delivery_move_ids
        all_procurements = []
        for line in wizard_lines:
            all_procurements += self._prepare_replace_procurements(
                warehouse, scheduled_date, line.product_id, line.qty, line.product_uom
            )
        if all_procurements:
            self.env["procurement.group"].run(all_procurements)
        new_moves = self.delivery_move_ids - moves_before
        body = ""
        for new_move in new_moves:
            body += Markup(
                _(
                    'Replacement: Move <a href="#" data-oe-model="stock.move"'
                    ' data-oe-id="%(move_id)d">%(move_name)s</a> (Picking <a'
                    ' href="#" data-oe-model="stock.picking"'
                    ' data-oe-id="%(picking_id)d"> %(picking_name)s</a>) has'
                    " been created."
                )
                % {
                    "move_id": new_move.id,
                    "move_name": new_move.display_name,
                    "picking_id": new_move.picking_id.id,
                    "picking_name": new_move.picking_id.name,
                }
                + "\n"
            )
        self._add_replace_message(body, None, None)
        self.write({"state": "waiting_replacement"})

    def create_replace(self, scheduled_date, warehouse, product, qty, uom):
        """Intended to be invoked by the delivery wizard"""
        self._ensure_can_be_replaced()
        moves_before = self.delivery_move_ids
        procurements = self._prepare_replace_procurements(
            warehouse, scheduled_date, product, qty, uom
        )
        if procurements:
            self.env["procurement.group"].run(procurements)
        new_moves = self.delivery_move_ids - moves_before
        body = ""
        # The product replacement could explode into several moves like in the case of
        # MRP BoM Kits
        for new_move in new_moves:
            body += Markup(
                _(
                    'Replacement: Move <a href="#" data-oe-model="stock.move"'
                    ' data-oe-id="%(move_id)d">%(move_name)s</a> (Picking <a'
                    ' href="#" data-oe-model="stock.picking"'
                    ' data-oe-id="%(picking_id)d"> %(picking_name)s</a>) has'
                    " been created."
                )
                % (
                    {
                        "move_id": new_move.id,
                        "move_name": new_move.display_name,
                        "picking_id": new_move.picking_id.id,
                        "picking_name": new_move.picking_id.name,
                    }
                )
                + "\n"
            )
        for rma in self:
            rma._add_replace_message(body, qty, uom)
        self.write({"state": "waiting_replacement"})

    def _add_replace_message(self, body, qty, uom):
        self.ensure_one()
        self.message_post(
            body=body
            # TODO: Verify how to replace this line
            # or Markup(
            #     _(
            #         "Replacement:<br/>"
            #         'Product <a href="#" data-oe-model="product.product" '
            #         'data-oe-id="%(id)d">%(name)s</a><br/>'
            #         "Quantity %(qty)s %(uom)s<br/>"
            #         "This replacement did not create a new move, but one of "
            #         "the previously created moves was updated with this data."
            #     )
            #     % (
            #         {
            #             "id": self.product_id.id,
            #             "name": self.product_id.display_name,
            #             "qty": qty,
            #             "uom": uom.name,
            #         }
            #     )
            # )
        )

    # Mail business methods
    def _creation_subtype(self):
        if self.state in ("draft"):
            return self.env.ref("rma.mt_rma_draft")
        else:
            return super()._creation_subtype()

    def _track_subtype(self, init_values):
        self.ensure_one()
        if "state" in init_values:
            if self.state == "draft":
                return self.env.ref("rma.mt_rma_draft")
            elif self.state == "confirmed":
                return self.env.ref("rma.mt_rma_notification")
        return super()._track_subtype(init_values)

    def message_new(self, msg_dict, custom_values=None):
        """Extract the needed values from an incoming rma emails data-set
        to be used to create an RMA.
        """
        if custom_values is None:
            custom_values = {}
        subject = msg_dict.get("subject", "")
        body = html2plaintext(msg_dict.get("body", ""))
        desc = _(
            "<b>E-mail subject:</b> %(subject)s<br/><br/><b>E-mail"
            " body:</b><br/>%(body)s"
        ) % ({"subject": subject, "body": body})
        defaults = {
            "description": desc,
            "name": _("New"),
            "origin": _("Incoming e-mail"),
        }
        if msg_dict.get("author_id"):
            partner = self.env["res.partner"].browse(msg_dict.get("author_id"))
            defaults.update(
                partner_id=partner.id,
                partner_invoice_id=partner.address_get(["invoice"]).get(
                    "invoice", False
                ),
            )
        if msg_dict.get("priority"):
            defaults["priority"] = msg_dict.get("priority")
        defaults.update(custom_values)
        rma = super().message_new(msg_dict, custom_values=defaults)
        if rma.user_id and rma.user_id.partner_id not in rma.message_partner_ids:
            rma.message_subscribe([rma.user_id.partner_id.id])
        return rma

    @api.returns("mail.message", lambda value: value.id)
    def message_post(self, **kwargs):
        """Set 'sent' field to True when an email is sent from rma form
        view. This field (sent) is used to set the appropriate style to the
        'Send by Email' button in the rma form view.
        """
        if self.env.context.get("mark_rma_as_sent"):
            self.write({"sent": True})
        # mail_post_autofollow=True to include email recipient contacts as
        # RMA followers
        self_with_context = self.with_context(mail_post_autofollow=True)
        return super(Rma, self_with_context).message_post(**kwargs)

    def _message_get_suggested_recipients(self):
        recipients = super()._message_get_suggested_recipients()
        try:
            for record in self.filtered("partner_id"):
                record._message_add_suggested_recipient(
                    recipients, partner=record.partner_id, reason=_("Customer")
                )
        except AccessError as e:  # no read access rights
            _logger.debug(e)
        return recipients

    # Reporting business methods
    def _get_report_base_filename(self):
        self.ensure_one()
        return "RMA Report - %s" % self.name

    # Other business methods

    def update_received_state_on_reception(self):
        """Invoked by:
        [stock.move]._action_done
        Here we can attach methods to trigger when the customer products
        are received on the RMA location, such as automatic notifications
        """
        fully_received = self.filtered(
            lambda r: all(
                m.state == "done"
                for m in (r.reception_move_ids | r.reception_move_id)
                if m
            )
        )
        fully_received.write({"state": "received"})
        self._send_receipt_confirmation_email()
        # TODO: Implement the logic
        # for rec in self:
        #     if rec.operation_id.action_create_delivery == "automatic_after_receipt":
        #         rec.with_context(
        #             rma_return_grouping=rec.env.company.rma_return_grouping
        #         ).create_replace(
        #             fields.Datetime.now(),
        #             rec.warehouse_id,
        #             rec.product_id,
        #             rec.product_uom_qty,
        #             rec.product_uom,
        #         )
        #
        #     if rec.operation_id.action_create_refund == "automatic_after_receipt":
        #         rec.action_refund()

    def update_received_state(self):
        """Invoked by:
        [stock.move].unlink
        [stock.move]._action_cancel
        """
        rma = self.filtered(lambda r: r.delivered_qty == 0)
        if rma:
            rma.write({"state": "received"})

    def update_replaced_state(self):
        """Invoked by:
        [stock.move]._action_done
        [stock.move].unlink
        [stock.move]._action_cancel
        """
        rma = self.filtered(
            lambda r: (r.state == "waiting_replacement" and 0 >= r.remaining_qty)
        )
        if rma:
            rma.write({"state": "replaced"})

    def update_returned_state(self):
        """Invoked by [stock.move]._action_done"""
        rma = self.filtered(
            lambda r: (r.state == "waiting_return" and r.remaining_qty <= 0)
        )
        if rma:
            rma.write({"state": "returned"})

    def _delivery_group_key(self):
        warnings.warn(
            "_delivery_group_key is deprecated and will be removed in the future. "
            "Use _get_delivery_group_key instead",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self._get_delivery_group_key()

    def _group_delivery_if_needed(self):
        warnings.warn(
            "_group_delivery_if_needed is deprecated and will be removed in the"
            " future. Use _assign_delivery_procurement_group instead",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self._assign_delivery_procurement_group()

    def action_update_taxes(self):
        # TODO: Implement the functionality
        pass

    def show_rma_invoices(self):
        # TODO: Implement the functionality
        pass
