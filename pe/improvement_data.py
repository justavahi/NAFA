"""Synthetic regression cases; group-disjoint partitions, never real client data."""
from .pipeline import CaseInput

# The partition is assigned to the entire scenario family before any model call.
FAMILIES=[
 ('dev','REG','CREDIT','ET-17','Поставщики обязаны перейти на новый порядок подтверждения счетов. Оплата неподтверждённых счетов приостанавливается.','Оптовая торговля с отсрочками платежа','negative'),
 ('dev','LABOR','DEMAND','ET-10','На региональном рынке вырос спрос на операторов производственных линий. Часть работодателей увеличила предложения оплаты.','Производитель упаковки с ручными операциями','negative'),
 ('dev','ENERGY','INFL','ET-07','Опубликовано решение об увеличении регулируемого тарифа на электроэнергию для предприятий. Условия переноса затрат в цены не указаны.','Холодильный склад с непрерывным энергопотреблением','negative'),
 ('dev','DEMAND','CPTY','ET-11','Крупная торговая сеть объявила расширение заказов у региональных производителей. Перечень выбранных поставщиков пока не опубликован.','Производитель готовой продукции','two_branches'),
 ('validation','COMMOD','FX','ET-08','Отраслевая сводка сообщает о снижении биржевых цен на промышленное сырьё. Валютная стоимость поставки зависит от условий расчётов.','Производитель изделий из металла','two_branches'),
 ('validation','BUDGET','CREDIT','ET-12','В регионе утверждена программа возмещения части затрат на модернизацию. Право конкретной компании на выплату не установлено.','Региональное пищевое производство','two_branches'),
 ('validation','CPTY','DEMAND','ET-14','Крупный заказчик сообщил о переносе сроков приёмки партий продукции. Отсрочка платежей по действующим договорам не раскрыта.','Поставщик комплектующих','negative'),
 ('test','GEOPOL','ENERGY','ET-16','Объявлены ограничения пропускной способности международного грузового маршрута. Перевозчики пересматривают графики поставок.','Дистрибьютор импортных компонентов','negative'),
 ('test','FX','CREDIT','ET-04','Расчётный банк изменил порядок подтверждения документов для валютных переводов. Направление изменения валютного курса не задано.','Компания с внешнеторговыми расчётами','two_branches'),
 ('test','INFL','DEMAND','ET-03','Отраслевой обзор сообщает о снижении отпускных цен при сохранении затрат части производителей. Данные о договорах конкретных компаний отсутствуют.','Производитель расходных материалов','two_branches'),
]
VARIANTS=[
 ('complete','Есть кредитная линия для оборотных средств; используются расчётные услуги.','Условия индексации цен и сроки оплаты не подтверждены.'),
 ('sparse',None,'Наличие обязательств и условия договоров неизвестны.'),
 ('mixed','Есть кредит и депозит; их суммы и сроки не раскрыты.','Баланс расходов и доходов по обязательствам неизвестен.'),
 ('no_debt','Кредитов по предоставленным данным нет; есть расчётный счёт.','Влияние на оборотный капитал зависит от договоров с заказчиками.'),
 ('currency_unknown','Есть флаг внешнеторговых операций, направление не установлено.','Экспорт или импорт и валюта цен неизвестны.'),
 ('fixed_contract','У части действующих договоров фиксированы цены.','Сроки пересмотра и охват фиксированных договоров не раскрыты.'),
]


def new_cases():
    records=[]
    for family,(split,main,secondary,event_type,news,sector,sign) in enumerate(FAMILIES):
        for variant,(segment,products,conditions) in enumerate(VARIANTS):
            cid=f'NX-{family:02}-{variant:02}';client=f'NEW_CLIENT_{family:02}_{variant:02}'
            story=f'NEW_STORY_{family:02}'
            text='Синтетический учебный сценарий, не реальная новость. '+news
            entries=[dict(id='N1',kind='NEWS_FACT',source=dict(document_id='NEWS',start=0,end=len(text),quote=text))]
            for eid,metric,value in [('C1','F-08',products),('C2','F-03',sector),('C3','F-13',conditions)]:
                entries.append(dict(id=eid,kind='COMPANY_FEATURE',client_id=client,feature_id=metric,value=value,source_record_id=f'synthetic/{cid}/{eid}'))
            for eid,fid,value,inputs in [('D_REL','FM-06',.7,['C2','N1']),('D_CONF','FM-20',.4 if segment=='sparse' else .75,['C1','C3','N1'])]:
                entries.append(dict(id=eid,kind='COMPUTED_VALUE',formula_id=fid,value=value,input_ids=inputs,calculation_ref=f'synthetic_fixture/{cid}/{fid}'))
            effects={main:sign,secondary:'two_branches'}
            if segment in {'sparse','mixed','currency_unknown','fixed_contract'}:effects[main]='two_branches'
            if segment=='no_debt' and secondary=='CREDIT':effects[secondary]='neutral'
            for factor,effect in effects.items():
                entries.append(dict(id='D_'+factor,kind='COMPUTED_VALUE',formula_id='FM-14',value=effect,
                    input_ids=['N1','C1','C2','C3'],calculation_ref=f'synthetic_fixture/{cid}/declared_sign/{factor}'))
            case=CaseInput.model_validate(dict(ledger=dict(trace_id='experiment_'+cid,client_id=client,event_id=story,
                documents=[dict(id='NEWS',kind='news',text=text,as_of='2026-10-01',is_mock=True)],entries=entries),
                event=dict(event_type=event_type,entities=[],facts=[],iota={main:.8,secondary:.4},direction={main:0,secondary:0},
                    severity=3,geo_scope='regional',horizon='не задан',rationale='Synthetic evaluation fixture; no extraction/DA accuracy claim'),
                persona='PER-03',bank_view_status='missing',relevance_id='D_REL',confidence_id='D_CONF',
                effect_source_ids={f:'D_'+f for f in effects}))
            records.append(dict(case_id=cid,split=split,source_group=story,client_group=client,segment=segment,
                provenance='synthetic_new_regressions_not_real_external_holdout',case=case.model_dump(mode='json')))
    return records
