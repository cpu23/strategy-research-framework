#property strict
#property version   "1.000"
#property description "Range breakout pullback baseline EA for MT5 research."

#include <Trade/Trade.mqh>

input string InpStrategyId = "range_breakout_pullback_v1";
input long   InpMagicNumber = 240001;
input int    InpRangeLookbackBars = 20;
input int    InpAtrPeriod = 14;
input double InpStopAtrBuffer = 0.25;
input double InpRiskPercentOfStartingBalance = 0.25;
input double InpFixedRiskMoney = 0.0;
input double InpRiskReward = 1.50;
input int    InpMaxHoldBars = 12;
input int    InpStartHour = 0;
input int    InpEndHour = 24;
input bool   InpUseBlockedHours = false;
input int    InpBlockedStartHour = 12;
input int    InpBlockedEndHour = 17;
input int    InpSlippagePoints = 20;
input bool   InpAllowLong = true;
input bool   InpAllowShort = true;
input bool   InpCloseAtEndHour = false;
input bool   InpUseImpulseFilter = false;
input int    InpImpulseLookbackBars = 12;
input double InpMinImpulseAtr = 4.0;
input bool   InpUseMaxRetestDepthFilter = false;
input double InpMaxRetestDepthAtr = 0.10;

CTrade trade;
double starting_balance = 0.0;
datetime last_bar_time = 0;
int atr_handle = INVALID_HANDLE;
int trade_log_handle = INVALID_HANDLE;
int equity_log_handle = INVALID_HANDLE;
int bar_log_handle = INVALID_HANDLE;
bool history_written = false;

string LogPrefix()
{
   return InpStrategyId + "_" + _Symbol + "_" + EnumToString(_Period);
}

bool IsWithinTradingHours()
{
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);

   bool within_main_window = true;

   if(InpStartHour != InpEndHour)
   {
      if(InpStartHour < InpEndHour)
         within_main_window = tm.hour >= InpStartHour && tm.hour < InpEndHour;
      else
         within_main_window = tm.hour >= InpStartHour || tm.hour < InpEndHour;
   }

   if(!within_main_window)
      return false;

   if(!InpUseBlockedHours || InpBlockedStartHour == InpBlockedEndHour)
      return true;

   if(InpBlockedStartHour < InpBlockedEndHour)
      return !(tm.hour >= InpBlockedStartHour && tm.hour < InpBlockedEndHour);

   return !(tm.hour >= InpBlockedStartHour || tm.hour < InpBlockedEndHour);
}

bool SelectStrategyPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return true;
   }

   return false;
}

double NormalizeVolume(double lots)
{
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      return lots;

   lots = MathMax(min_lot, MathMin(max_lot, lots));
   lots = MathFloor(lots / step) * step;
   return NormalizeDouble(lots, 2);
}

double PositionSizeForRisk(double entry, double stop)
{
   double risk_money = InpFixedRiskMoney > 0.0
      ? InpFixedRiskMoney
      : starting_balance * InpRiskPercentOfStartingBalance / 100.0;

   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double stop_distance = MathAbs(entry - stop);

   if(risk_money <= 0.0 || tick_size <= 0.0 || tick_value <= 0.0 || stop_distance <= 0.0)
      return 0.0;

   double risk_per_lot = (stop_distance / tick_size) * tick_value;
   if(risk_per_lot <= 0.0)
      return 0.0;

   return NormalizeVolume(risk_money / risk_per_lot);
}

void CloseStrategyPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         trade.PositionClose(ticket);
      }
   }
}

void ManageOpenPosition()
{
   if(InpMaxHoldBars <= 0 || !SelectStrategyPosition())
      return;

   datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
   int seconds_per_bar = PeriodSeconds(_Period);
   if(seconds_per_bar <= 0)
      return;

   int bars_held = (int)((TimeCurrent() - open_time) / seconds_per_bar);
   if(bars_held >= InpMaxHoldBars)
      trade.PositionClose(PositionGetInteger(POSITION_TICKET));
}

void LogEquity()
{
   if(equity_log_handle == INVALID_HANDLE)
      return;

   FileWrite(
      equity_log_handle,
      TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY)
   );
}

void LogBar()
{
   if(bar_log_handle == INVALID_HANDLE || Bars(_Symbol, _Period) < 3)
      return;

   FileWrite(
      bar_log_handle,
      TimeToString(iTime(_Symbol, _Period, 1), TIME_DATE | TIME_SECONDS),
      iOpen(_Symbol, _Period, 1),
      iHigh(_Symbol, _Period, 1),
      iLow(_Symbol, _Period, 1),
      iClose(_Symbol, _Period, 1),
      iVolume(_Symbol, _Period, 1)
   );
}

double DealNetProfit(ulong deal)
{
   return HistoryDealGetDouble(deal, DEAL_PROFIT)
      + HistoryDealGetDouble(deal, DEAL_SWAP)
      + HistoryDealGetDouble(deal, DEAL_COMMISSION);
}

bool FindEntryDeal(
   long position_id,
   datetime &entry_time,
   string &direction,
   double &entry_price
)
{
   int total = HistoryDealsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagicNumber)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_POSITION_ID) != position_id)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;

      direction = HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY ? "long" : "short";
      entry_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      entry_price = HistoryDealGetDouble(deal, DEAL_PRICE);
      return true;
   }

   return false;
}

void WriteTradeHistory()
{
   if(history_written || trade_log_handle == INVALID_HANDLE)
      return;

   history_written = true;

   if(!HistorySelect(0, TimeCurrent() + PeriodSeconds(_Period)))
      return;

   double planned_risk = InpFixedRiskMoney > 0.0
      ? InpFixedRiskMoney
      : starting_balance * InpRiskPercentOfStartingBalance / 100.0;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagicNumber)
         continue;

      long entry_type = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_OUT_BY)
         continue;

      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      datetime entry_time = 0;
      string direction = "";
      double entry_price = 0.0;

      FindEntryDeal(position_id, entry_time, direction, entry_price);

      double profit = DealNetProfit(deal);
      double r_multiple = planned_risk > 0.0 ? profit / planned_risk : 0.0;

      FileWrite(
         trade_log_handle,
         InpStrategyId,
         _Symbol,
         EnumToString(_Period),
         entry_time > 0 ? TimeToString(entry_time, TIME_DATE | TIME_SECONDS) : "",
         TimeToString((datetime)HistoryDealGetInteger(deal, DEAL_TIME), TIME_DATE | TIME_SECONDS),
         direction,
         entry_price > 0.0 ? DoubleToString(entry_price, _Digits) : "",
         HistoryDealGetDouble(deal, DEAL_PRICE),
         "",
         "",
         HistoryDealGetDouble(deal, DEAL_VOLUME),
         profit,
         r_multiple,
         "closed"
      );
   }
}

bool CurrentAtr(double &atr)
{
   double values[];
   ArraySetAsSeries(values, true);

   if(CopyBuffer(atr_handle, 0, 1, 1, values) != 1)
      return false;

   atr = values[0];
   return atr > 0.0;
}

bool PassesImpulseFilter(const int direction, const double pullback_close, const double atr)
{
   if(!InpUseImpulseFilter)
      return true;

   if(InpImpulseLookbackBars <= 0 || InpMinImpulseAtr <= 0.0)
      return true;

   if(Bars(_Symbol, _Period) < InpImpulseLookbackBars + 3)
      return false;

   double prior_close = iClose(_Symbol, _Period, 1 + InpImpulseLookbackBars);
   if(prior_close <= 0.0 || atr <= 0.0)
      return false;

   double directional_impulse_atr = direction * (pullback_close - prior_close) / atr;
   return directional_impulse_atr >= InpMinImpulseAtr;
}

bool PassesMaxRetestDepthFilter(const int direction, const double boundary, const double pullback_extreme, const double atr)
{
   if(!InpUseMaxRetestDepthFilter)
      return true;

   if(InpMaxRetestDepthAtr <= 0.0 || atr <= 0.0)
      return true;

   double retest_depth_atr = direction > 0
      ? (boundary - pullback_extreme) / atr
      : (pullback_extreme - boundary) / atr;

   return retest_depth_atr <= InpMaxRetestDepthAtr;
}

bool BuildSignal(int &direction, double &entry, double &stop, double &target)
{
   if(Bars(_Symbol, _Period) < InpRangeLookbackBars + 5)
      return false;

   int range_high_index = iHighest(_Symbol, _Period, MODE_HIGH, InpRangeLookbackBars, 3);
   int range_low_index = iLowest(_Symbol, _Period, MODE_LOW, InpRangeLookbackBars, 3);
   if(range_high_index < 0 || range_low_index < 0)
      return false;

   double range_high = iHigh(_Symbol, _Period, range_high_index);
   double range_low = iLow(_Symbol, _Period, range_low_index);
   if(range_high <= range_low)
      return false;

   double breakout_close = iClose(_Symbol, _Period, 2);
   double pullback_high = iHigh(_Symbol, _Period, 1);
   double pullback_low = iLow(_Symbol, _Period, 1);
   double pullback_close = iClose(_Symbol, _Period, 1);
   double atr = 0.0;
   if(!CurrentAtr(atr))
      return false;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(InpAllowLong &&
      breakout_close > range_high &&
      pullback_low <= range_high &&
      pullback_close > range_high &&
      PassesImpulseFilter(1, pullback_close, atr) &&
      PassesMaxRetestDepthFilter(1, range_high, pullback_low, atr))
   {
      direction = 1;
      entry = ask;
      stop = MathMin(pullback_low, range_high - atr * InpStopAtrBuffer);
      target = entry + (entry - stop) * InpRiskReward;
      return stop > 0.0 && stop < entry && target > entry;
   }

   if(InpAllowShort &&
      breakout_close < range_low &&
      pullback_high >= range_low &&
      pullback_close < range_low &&
      PassesImpulseFilter(-1, pullback_close, atr) &&
      PassesMaxRetestDepthFilter(-1, range_low, pullback_high, atr))
   {
      direction = -1;
      entry = bid;
      stop = MathMax(pullback_high, range_low + atr * InpStopAtrBuffer);
      target = entry - (stop - entry) * InpRiskReward;
      return stop > entry && target > 0.0 && target < entry;
   }

   return false;
}

int OnInit()
{
   starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

   atr_handle = iATR(_Symbol, _Period, InpAtrPeriod);
   if(atr_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade_log_handle = FileOpen(LogPrefix() + "_trades.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(trade_log_handle != INVALID_HANDLE)
   {
      FileWrite(
         trade_log_handle,
         "strategy_id",
         "symbol",
         "timeframe",
         "entry_time",
         "exit_time",
         "direction",
         "entry_price",
         "exit_price",
         "stop_price",
         "target_price",
         "volume",
         "profit",
         "r_multiple",
         "exit_reason"
      );
   }

   equity_log_handle = FileOpen(LogPrefix() + "_equity.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(equity_log_handle != INVALID_HANDLE)
      FileWrite(equity_log_handle, "time", "balance", "equity");

   bar_log_handle = FileOpen(LogPrefix() + "_bars.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(bar_log_handle != INVALID_HANDLE)
      FileWrite(bar_log_handle, "time", "open", "high", "low", "close", "tick_volume");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   WriteTradeHistory();
   LogEquity();

   if(trade_log_handle != INVALID_HANDLE)
      FileClose(trade_log_handle);

   if(equity_log_handle != INVALID_HANDLE)
      FileClose(equity_log_handle);

   if(bar_log_handle != INVALID_HANDLE)
      FileClose(bar_log_handle);

   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);
}

double OnTester()
{
   LogEquity();
   return AccountInfoDouble(ACCOUNT_BALANCE);
}

void OnTick()
{
   datetime current_bar = iTime(_Symbol, _Period, 0);
   if(current_bar == last_bar_time)
      return;

   last_bar_time = current_bar;
   LogBar();
   LogEquity();
   ManageOpenPosition();

   if(!IsWithinTradingHours())
   {
      if(InpCloseAtEndHour)
         CloseStrategyPositions();
      return;
   }

   if(SelectStrategyPosition())
      return;

   int direction = 0;
   double entry = 0.0;
   double stop = 0.0;
   double target = 0.0;

   if(!BuildSignal(direction, entry, stop, target))
      return;

   double volume = PositionSizeForRisk(entry, stop);
   if(volume <= 0.0)
      return;

   if(direction > 0)
      trade.Buy(volume, _Symbol, entry, stop, target, InpStrategyId);
   else if(direction < 0)
      trade.Sell(volume, _Symbol, entry, stop, target, InpStrategyId);
}
