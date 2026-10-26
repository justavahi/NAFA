import pytest
from pe.participant import client_type,products_text


@pytest.mark.parametrize('value,expected',[('CLIENT','existing'),('client','existing'),('PROSPECT','potential'),('CLOSE_CLIENT',None),(None,None),('unexpected',None)])
def test_observed_type_dictionary_is_exact_and_closed_client_not_guessed(value,expected):
    assert client_type(value)==expected


def test_product_zero_is_not_claimed_to_mean_no_debt_anywhere():
    text=products_text({'LOA':0,'DEP':None})
    assert 'не доказывает отсутствие' in text and 'других банках' in text and 'null' in text
