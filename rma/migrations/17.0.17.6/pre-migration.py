from openupgradelib import openupgrade


_MODEL_TO_RENAMED_FIELDS = {
    "rma": [
        ("sale_order_id", "order_id"),
    ]
}

_RENAME_FIELDS = {
    module.replace('.', '_'): fields_list for module, fields_list in _MODEL_TO_RENAMED_FIELDS.items()
}

@openupgrade.migrate()
def migrate(env, version):
    env['ir.ui.view'].search([('model', '=', 'rma')]).unlink()
    openupgrade.rename_columns(env.cr, _RENAME_FIELDS)
    openupgrade.rename_fields(
        env,
        [
            (
                model_name,
                model_name.replace(".", "_"),
                field_spec[0],
                field_spec[1],
            )
            for model_name, field_specs in _MODEL_TO_RENAMED_FIELDS.items()
            for field_spec in field_specs
        ],
    )
