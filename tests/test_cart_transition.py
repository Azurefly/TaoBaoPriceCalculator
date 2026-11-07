import asyncio
import pytest
from app.cart_transition import row_signature, wait_for_group


def frame(group, line, **extra):
    return {'profile':'fixture','active_group_id':group,'items':[{'line_id':line,'quantity':1,'sku':'s','group_ids':[group]}],**extra}


def run_frames(frames, before, attempts=8):
    calls=[]
    async def read():
        index=min(len(calls),len(frames)-1);calls.append(index);return frames[index]
    async def pause(ms):
        assert ms==350
    return asyncio.run(wait_for_group(read,pause,'new',before,attempts=attempts)),calls


def test_group_signature_ignores_derived_tab_membership():
    assert row_signature(frame('old','a'))==row_signature(frame('new','a'))


def test_group_waits_for_changed_and_stable_rows():
    result,calls=run_frames([frame('new','a'),frame('new','b',loading=True),frame('new','b'),frame('new','b'),frame('new','b')],frame('old','a'))
    assert result['items'][0]['line_id']=='b' and len(calls)==5


def test_changed_tab_with_old_rows_is_not_success():
    with pytest.raises(ValueError,match='旧列表'):
        run_frames([frame('new','a')],frame('old','a'),attempts=3)


def test_group_wait_restarts_stability_after_error():
    result,calls=run_frames([frame('new','b'),frame('new','b',errors=['duplicate']),frame('new','c'),frame('new','c'),frame('new','c')],frame('old','a'))
    assert result['items'][0]['line_id']=='c' and len(calls)==5
