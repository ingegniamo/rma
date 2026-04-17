from odoo import fields, models, api

import logging

_logger = logging.getLogger(__name__)

PEPPERI_DATETIME_TZ = "%Y-%m-%dT%H:%M:%SZ"
PEPPERI_DATE_Z = "%Y-%m-%dZ"


class RmaLine(models.Model):
    _name = 'rma.line'
    _description = 'RMA line'

    account_line_id = fields.Many2one(
        comodel_name='account.move.line',
        ondelete='set null',
        string='Fattura',
        copy=False
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        ondelete='set null',
        string='Company',
        copy=False
    )
    description = fields.Text(
        string='Description',
        copy=False
    )
    discount = fields.Float(
        string='Sconto',
        copy=False
    )
    move_id = fields.Many2one(
        comodel_name='stock.move',
        ondelete='set null',
        string='Movimento',
        index=True,
        copy=False
    )
    move_origin_id = fields.Many2one(
        comodel_name='stock.move',
        ondelete='set null',
        string='Movimento origine',
        index=True,
        copy=False
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Customer',
        related='rma_id.partner_id',
        store=True,
        readonly=True,
        copy=False
    )
    currency_id = fields.Many2one(
        related='rma_id.currency_id'
    )
    price_subtotal = fields.Float(
        string='Prezzo totale',
        readonly=True,
        copy=False
    )
    price_unit = fields.Float(
        string='Prezzo unitario',
        copy=False
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        ondelete='set null',
        string='Product',
        copy=False
    )
    product_uom = fields.Many2one(
        comodel_name='uom.uom',
        ondelete='restrict',
        string='UoM',
        required=True,
        copy=False
    )
    qty = fields.Float(
        string='Qty',
        copy=False
    )
    return_move_ids = fields.One2many(
        comodel_name='stock.move',
        inverse_name='rma_line_id',
        string='Movimenti',
        copy=False
    )
    rma_id = fields.Many2one(
        comodel_name='rma',
        ondelete='set null',
        string='Rma',
        copy=False
    )
    sale_line_id = fields.Many2one(
        comodel_name='sale.order.line',
        ondelete='set null',
        string='Sale Line',
        copy=False
    )
    state = fields.Selection([
        ('to_approve', 'Da Approvare'),
        ('approved', 'Approvata')
    ],
        string='State',
        copy=False
    )
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        relation='account_tax_rma_line_rel',
        column1='rma_line_id',
        column2='account_tax_id',
        string='Taxes',
        copy=False
    )
    image_master_id = fields.Binary(
        related='product_id.image_variant_1920',
        store=True
    )

    @api.onchange('product_id')
    def _onchange_product_id_uom(self):
        if self.product_id:
            self.product_uom = self.product_id.uom_id

    @api.onchange('sale_line_id')
    def onchange_sale_line_id(self):
        if self.sale_line_id:
            self.write({
                'product_id': self.sale_line_id.product_id,
                'description': self.sale_line_id.name,
                'qty': self.sale_line_id.product_uom_qty,
                'tax_ids': self.sale_line_id.tax_id,
                'price_unit': self.sale_line_id.price_unit,
                'discount': self.sale_line_id.discount
            })

    @api.onchange('account_line_id')
    def onchange_account_line_id(self):
        if self.account_line_id:
            self.write({
                'product_id': self.account_line_id.product_id,
                'description': self.account_line_id.name,
                'qty': self.account_line_id.quantity,
                'tax_ids': self.account_line_id.tax_ids,
                'price_unit': self.account_line_id.price_unit,
                'discount': self.account_line_id.discount,
            })

    def _product_is_storable(self, product=None):
        product = product or self.product_id
        return product.type in ["product", "consu"]

    def _prepare_reception_procurement_vals(self, group=None):
        """This method is used only for reception and a specific RMA IN route."""
        return {}
