import json
import unittest
from pathlib import Path
from nexustrade.portfolio import (portfolio,strategy,always,buy,sell_holdings,stock_asset,IndicatorAtCondition,PositionPercentChange,CANDIDATE,Value)
from nexustrade.portfolio import Price,CrossAbove,andThen

class StatefulControlsTests(unittest.TestCase):
    def test_trading_session_sequence_and_native_release_wait(self):
        step=Value(1)>0
        self.assertEqual(andThen(20,"TradingDay",step,step).to_dict()["window"],
            {"length":20,"interval":"TradingDay"})
        saved=andThen({"transition":{"minimumTradingMinutes":390,"waiting":True}},step,step).to_dict()
        self.assertNotIn("window",saved)
        self.assertEqual(saved["transition"],{"minimumTradingMinutes":390,"waiting":True})
    def test_minimum_conditional_capture_is_in_the_generated_builder(self):
        indicator=IndicatorAtCondition(Value(-7),always(),"Minimum","Calendar",0)
        self.assertEqual(indicator.to_dict()["selection"],"Minimum")

    def test_second_crossing_is_the_exact_indicator_exercised_by_the_rust_golden(self):
        def crossing():
            return CrossAbove(Price(stock_asset("IWM")),Value(100))>0
        indicator=IndicatorAtCondition(Price(stock_asset("QQQ")),
            andThen(60,"Minute",crossing(),crossing()),"First","Calendar",0)
        path=Path(__file__).resolve().parents[3]/"app/src/model/indicators/testdata/conditional_capture.golden.json"
        golden=json.loads(path.read_text())
        self.assertEqual(indicator.to_dict(),golden["secondCrossing"]["indicator"])

    def test_builders_serialize_compositional_trading_rules(self):
        protection=IndicatorAtCondition(Value(10),always(),"Maximum","Calendar",0)
        weak=PositionPercentChange(CANDIDATE)<-1
        book=portfolio("Controls",[
            strategy(4,protection<1,buy(stock_asset("SPY"),2500,"dollars",execution_priority=4,funding={
                "condition":weak,"rankBy":PositionPercentChange(CANDIDATE),"direction":"Lowest",
                "amount":{"type":"percent of current positions","amount":100}})),
            strategy("Protection",protection>0,sell_holdings(weak,execution_priority=0))],
            initial_value=60000,supports_fractional_shares=False,cash_settlement="T1")
        saved=json.loads(json.dumps(book))
        self.assertEqual(saved["strategies"][0]["condition"]["lhs"]["type"],"IndicatorAtCondition")
        self.assertEqual(saved["strategies"][0]["action"]["funding"]["condition"]["rhs"]["value"],-1)
        self.assertEqual(saved["strategies"][1]["action"]["holdingCondition"]["rhs"]["value"],-1)
        self.assertEqual(saved["cashSettlement"],"T1")
        self.assertNotIn("gainProtection",saved)
        self.assertNotIn("cashFunding",saved)
