import json
import game


def test_character_capacity_keeps_other_accounts_at_defaults(monkeypatch):
    monkeypatch.setattr(game.db,'kv_get',lambda c,k:json.dumps({'BagSlots':4096,'BankSlots':4096}) if k=='inventory_capacity:8' else None)
    assert game.storage_capacity(None,8)==4096
    assert game.storage_capacity(None,8,banked=True)==4096
    assert game.storage_capacity(None,9)==game.DEFAULT_BAG_SLOTS
    assert game.storage_capacity(None,9,banked=True)==game.DEFAULT_BANK_SLOTS


def test_malformed_or_unbounded_capacity_cannot_remove_default_slots(monkeypatch):
    for raw in ['null','[]','broken','{"BagSlots":true}','{"BagSlots":"4096"}','{"BagSlots":-1}']:
        monkeypatch.setattr(game.db,'kv_get',lambda c,k:raw)
        assert game.storage_capacity(None,8)==game.DEFAULT_BAG_SLOTS
    monkeypatch.setattr(game.db,'kv_get',lambda c,k:'{"BagSlots":99999999}')
    assert game.storage_capacity(None,8)==20000


def test_withdraw_enforces_each_characters_capacity(monkeypatch):
    class Cursor:
        def fetchone(self):return None
    class Conn:
        def execute(self,*args):return Cursor()
        def commit(self):pass
    moved=[]
    monkeypatch.setattr(game.db,'kv_get',lambda c,k:'{"BagSlots":4096}' if k=='inventory_capacity:8' else None)
    monkeypatch.setattr(game,'_slots_used',lambda *a,**k:500)
    monkeypatch.setattr(game,'_move_rows',lambda c,cid,iid,to_banked:moved.append(cid) or 1)
    assert game.bank_withdraw(Conn(),{'id':8},123)=={'Cmd':'BankToInv','bankID':123}
    assert game.bank_withdraw(Conn(),{'id':9},123) is None
    assert moved==[8]
