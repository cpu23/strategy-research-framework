#property strict
#property version   "1.000"
#property description "Range Expansion Breakout V1 — ADX compression/expansion breakout."

#include <Trade/Trade.mqh>

input string InpStrategyId = "range_expansion_breakout_v1";
input long   InpMagicNumber = 250001;
input double InpRiskPercentOfStartingBalance = 0.50;
input double InpFixedRiskMoney = 0.0;
input double InpRiskReward = 3.0;
input int    InpCompressionBars = 20;
input int    InpAdxPeriod = 14;
input double InpAdxCompressionMax = 20.0;
input double InpAdxExpansionMin = 25.0;
input int    InpAtrPeriod = 14;
input double InpAtrMultiplierStop = 2.0;
input int    InpTimeStopBars = 12;
input int    InpSessionStartHour = 2;
input int    InpSessionEndHour = 17;
input int    InpSlippagePoints = 20;
input bool   InpAllowLong = true;
input bool   InpAllowShort = true;
input bool   InpCloseAtEndHour = true;
input bool   InpUseTrailingStop = true;
input bool   InpUseTimeStop = true;

CTrade trade;
double starting_balance = 0.0;
datetime last_bar_time = 0;
int trade_log_handle = INVALID_HANDLE;
int equity_log_handle = INVALID_HANDLE;
int bar_log_handle = INVALID_HANDLE;
bool history_written = false;

int adx_handle = INVALID_HANDLE;
datetime entry_bar_time = 0;

string LogPrefix()
{
   return InpStrategyId + "_" + _Symbol + "_" + EnumToString(_Period);
}

bool IsWithinTradingHours()
{
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);

   if(InpSessionStartHour == InpSessionEndHour)
      return true;

   if(InpSessionStartHour < InpSessionEndHour)
      return tm.hour >= InpSessionStartHour && tm.hour < InpSessionEndHour;

   return tm.hour >= InpSessionStartHour || tm.hour < InpSessionEndHour;
}

bool HasOpenPosition()
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

bool FindEntryDeal(long position_id, datetime &entry_time, string &direction, double &entry_price)
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

      datetime entry_time = 0;
      string direction = "";
      double entry_price = 0.0;
      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
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

double TrueRange(int shift)
{
   double high = iHigh(_Symbol, _Period, shift);
   double low = iLow(_Symbol, _Period, shift);
   double prev_close = iClose(_Symbol, _Period, shift + 1);

   return MathMax(
      high - low,
      MathMax(MathAbs(high - prev_close), MathAbs(low - prev_close))
   );
}

double SimpleAtr(int shift, int period)
{
   if(period <= 0)
      return 0.0;

   double sum = 0.0;
   for(int i = shift; i < shift + period; i++)
      sum += TrueRange(i);

   return sum / period;
}

void ManagePosition()
{
   double atr = SimpleAtr(1, InpAtrPeriod);
   if(atr <= 0.0)
      return;

   if(InpUseTimeStop && entry_bar_time > 0)
   {
      int bars_held = (int)((TimeCurrent() - entry_bar_time) / PeriodSeconds(_Period));
      if(bars_held >= InpTimeStopBars)
      {
         CloseStrategyPositions();
         return;
      }
   }

   if(!InpUseTrailingStop)
      return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double current_sl = PositionGetDouble(POSITION_SL);
      double current_tp = PositionGetDouble(POSITION_TP);
      double stop_distance = InpAtrMultiplierStop * atr;

      if(type == POSITION_TYPE_BUY)
      {
         double highest = iHigh(_Symbol, _Period, 1);
         for(int b = 2; b <= InpTimeStopBars; b++)
            highest = MathMax(highest, iHigh(_Symbol, _Period, b));

         double trail_sl = highest - stop_distance;
         if(trail_sl > current_sl || current_sl == 0.0)
         {
            trail_sl = NormalizeDouble(trail_sl, _Digits);
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(trail_sl < bid)
               trade.PositionModify(ticket, trail_sl, current_tp);
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double lowest = iLow(_Symbol, _Period, 1);
         for(int b = 2; b <= InpTimeStopBars; b++)
            lowest = MathMin(lowest, iLow(_Symbol, _Period, b));

         double trail_sl = lowest + stop_distance;
         if(trail_sl < current_sl || current_sl == 0.0)
         {
            trail_sl = NormalizeDouble(trail_sl, _Digits);
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            if(trail_sl > ask)
               trade.PositionModify(ticket, trail_sl, current_tp);
         }
      }
   }
}

bool BuildSignal(int &direction, double &entry, double &stop, double &target)
{
   if(Bars(_Symbol, _Period) < InpCompressionBars + InpAtrPeriod + 3)
      return false;

   if(adx_handle == INVALID_HANDLE)
      return false;

   double adx_buffer[];
   ArraySetAsSeries(adx_buffer, true);
   int copied = CopyBuffer(adx_handle, 0, 1, InpCompressionBars + 1, adx_buffer);
   if(copied < InpCompressionBars + 1)
      return false;

   double adx_sum = 0.0;
   for(int i = 1; i <= InpCompressionBars; i++)
   {
      if(adx_buffer[i] >= InpAdxCompressionMax)
         adx_sum += 1.0;
   }
   double adx_ratio = adx_sum / (double)InpCompressionBars;
   if(adx_ratio > 0.10)
      return false;

   if(adx_buffer[0] < InpAdxExpansionMin)
      return false;

   int signal_shift = 1;
   double signal_high = iHigh(_Symbol, _Period, signal_shift);
   double signal_low = iLow(_Symbol, _Period, signal_shift);
   double signal_close = iClose(_Symbol, _Period, signal_shift);
   double signal_open = iOpen(_Symbol, _Period, signal_shift);

   if(signal_high <= signal_low)
      return false;

   double range_high = iHigh(_Symbol, _Period, 2);
   double range_low = iLow(_Symbol, _Period, 2);
   for(int i = 3; i <= InpCompressionBars + 1; i++)
   {
      range_high = MathMax(range_high, iHigh(_Symbol, _Period, i));
      range_low = MathMin(range_low, iLow(_Symbol, _Period, i));
   }

   if(range_high <= range_low)
      return false;

   double atr = SimpleAtr(signal_shift, InpAtrPeriod);
   if(atr <= 0.0)
      return false;

   double stop_distance = InpAtrMultiplierStop * atr;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(signal_close > range_high)
   {
      direction = 1;
      entry = ask;
      stop = NormalizeDouble(entry - stop_distance, _Digits);
      if(entry <= stop)
         return false;
      target = NormalizeDouble(entry + (entry - stop) * InpRiskReward, _Digits);
      return true;
   }

   if(signal_close < range_low)
   {
      direction = -1;
      entry = bid;
      stop = NormalizeDouble(entry + stop_distance, _Digits);
      if(entry >= stop)
         return false;
      target = NormalizeDouble(entry - (stop - entry) * InpRiskReward, _Digits);
      return true;
   }

   return false;
}

int OnInit()
{
   starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);

   adx_handle = iADX(_Symbol, _Period, InpAdxPeriod);
   if(adx_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

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

   if(adx_handle != INVALID_HANDLE)
      IndicatorRelease(adx_handle);

   if(trade_log_handle != INVALID_HANDLE)
      FileClose(trade_log_handle);

   if(equity_log_handle != INVALID_HANDLE)
      FileClose(equity_log_handle);

   if(bar_log_handle != INVALID_HANDLE)
      FileClose(bar_log_handle);
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

   if(!IsWithinTradingHours())
   {
      if(InpCloseAtEndHour)
         CloseStrategyPositions();
      return;
   }

   if(HasOpenPosition())
   {
      ManagePosition();
      return;
   }

   int direction = 0;
   double entry = 0.0;
   double stop = 0.0;
   double target = 0.0;

   if(!BuildSignal(direction, entry, stop, target))
      return;

   if(direction > 0 && InpAllowLong)
   {
      double volume = PositionSizeForRisk(entry, stop);
      if(volume > 0.0)
      {
         if(trade.Buy(volume, _Symbol, entry, stop, target, InpStrategyId))
            entry_bar_time = last_bar_time;
      }
   }
   else if(direction < 0 && InpAllowShort)
   {
      double volume = PositionSizeForRisk(entry, stop);
      if(volume > 0.0)
      {
         if(trade.Sell(volume, _Symbol, entry, stop, target, InpStrategyId))
            entry_bar_time = last_bar_time;
      }
   }
}
