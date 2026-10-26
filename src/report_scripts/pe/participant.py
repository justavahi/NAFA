"""Conservative adaptation of real DA aggregates; no raw personal identifiers."""
def client_type(raw):
    if raw is None:return None
    return {'CLIENT':'existing','PROSPECT':'potential'}.get(str(raw).strip().upper())


def products_text(values):
    import json
    return ('Продуктовые флаги предоставленной банковской выгрузки: '+json.dumps(values,ensure_ascii=False)+
        '. Нулевой флаг не доказывает отсутствие обязательств в других банках; суммы, сроки и тип ставки не известны.')
