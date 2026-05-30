#property strict
#property version   "1.000"
#property description "VWAP Reversion 1H research EA."

#include <Trade/Trade.mqh>

input string InpStrategyId = "vwap_reversion_1h";
input long   InpMagicNumber = 230001;
input double InpRiskPercentOfStartingBalance = 0.50;
input double InpFixedRiskMoney = 0.0;
input int    InpVwapAnchorMode = 0;          // 0=session/day, 1=weekly
input int    InpSessionStartHour = 0;
input int    InpAtrPeriod = 14;
input double InpMinAtrPoints = 0.0;
input int    InpAdxPeriod = 14;
input double InpMaxAdx = 25.0;
input int    InpDeviationStdLookback = 40;
input double InpEntryZ = 2.0;
input double InpExitZ = 0.5;
input int    InpVwapSlopeBars = 5;
input double InpMaxVwapSlopeAtr = 0.20;
input int    InpVwapCrossLookback = 20;
input int    InpMinVwapCrosses = 2;
input int    InpFreshCloseLookback = 20;
input int    InpMaxHoldBars = 8;
input double InpAdverseAtrMultiplier = 1.20;
input int    InpMaxVwapBars = 240;
input bool   InpRequireH1OrH4 = true;
input int    InpStartHour = 0;
input int    InpEndHour = 24;
input int    InpSlippagePoints = 20;
input bool   InpAllowLong = true;
input bool   InpAllowShort = true;
input bool   InpCloseAtEndHour = false;

CTrade trade;
double starting_balance = 0.0;
datetime last_bar_time = 0;
int trade_log_handle = INVALID_HANDLE;
int equity_log_handle = INVALID_HANDLE;
int bar_log_handle = INVALID_HANDLE;
bool history_written = false;
int atr_handle = INVALID_HANDLE;
int adx_handle = INVALID_HANDLE;

ulong active_position_ticket = 0;
datetime active_entry_bar_time = 0;
long rule_exit_position_ids[];
string rule_exit_reasons[];

string LogPrefix()
{
   return InpStrategyId + "_" + _Symbol + "_" + EnumToString(_Period);
}

bool IsWithinTradingHours()
{
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);

   if(InpStartHour == InpEndHour)
      return true;

   if(InpStartHour < InpEndHour)
      return tm.hour >= InpStartHour && tm.hour < InpEndHour;

   return tm.hour >= InpStartHour || tm.hour < InpEndHour;
}

ulong FindStrategyPositionTicket()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return ticket;
   }

   return 0;
}

bool HasOpenPosition()
{
   return FindStrategyPositionTicket() != 0;
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

void RecordRuleExitReason(long position_id, string reason)
{
   if(position_id <= 0)
      return;

   int size = ArraySize(rule_exit_position_ids);
   for(int i = 0; i < size; i++)
   {
      if(rule_exit_position_ids[i] == position_id)
      {
         rule_exit_reasons[i] = reason;
         return;
      }
   }

   ArrayResize(rule_exit_position_ids, size + 1);
   ArrayResize(rule_exit_reasons, size + 1);
   rule_exit_position_ids[size] = position_id;
   rule_exit_reasons[size] = reason;
}

string RuleExitReasonForPosition(long position_id)
{
   int size = ArraySize(rule_exit_position_ids);
   for(int i = 0; i < size; i++)
   {
      if(rule_exit_position_ids[i] == position_id)
         return rule_exit_reasons[i];
   }

   return "rule_exit";
}

bool ClosePositionWithReason(ulong ticket, string reason)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   long position_id = PositionGetInteger(POSITION_IDENTIFIER);
   bool closed = trade.PositionClose(ticket);

   if(closed)
      RecordRuleExitReason(position_id, reason);

   return closed;
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
         ClosePositionWithReason(ticket, "end_hour_close");
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

string DealExitReason(ulong deal)
{
   long reason = HistoryDealGetInteger(deal, DEAL_REASON);

   if(reason == DEAL_REASON_EXPERT)
   {
      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      return RuleExitReasonForPosition(position_id);
   }

   return "closed";
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
         DealExitReason(deal)
      );
   }
}

bool CopyIndicatorValue(int handle, int buffer, int shift, double &value)
{
   double values[];
   ArraySetAsSeries(values, true);

   if(CopyBuffer(handle, buffer, shift, 1, values) != 1)
      return false;

   value = values[0];
   return value > 0.0;
}

int MaxLookbackNeeded()
{
   int needed = InpDeviationStdLookback + InpFreshCloseLookback + 5;
   needed = MathMax(needed, InpVwapCrossLookback + InpVwapSlopeBars + 5);
   needed = MathMax(needed, InpAdxPeriod * 3 + 5);
   needed = MathMax(needed, InpAtrPeriod * 3 + 5);
   needed = MathMax(needed, InpMaxVwapBars + 5);
   return needed;
}

datetime DailyAnchorStart(datetime bar_time)
{
   MqlDateTime tm;
   TimeToStruct(bar_time, tm);
   tm.min = 0;
   tm.sec = 0;
   int bounded_hour = MathMax(0, MathMin(23, InpSessionStartHour));

   if(tm.hour < bounded_hour)
   {
      datetime previous_day = bar_time - 86400;
      TimeToStruct(previous_day, tm);
   }

   tm.hour = bounded_hour;
   tm.min = 0;
   tm.sec = 0;
   return StructToTime(tm);
}

datetime WeeklyAnchorStart(datetime bar_time)
{
   MqlDateTime tm;
   TimeToStruct(bar_time, tm);
   int days_since_monday = tm.day_of_week == 0 ? 6 : tm.day_of_week - 1;
   datetime monday = bar_time - days_since_monday * 86400;
   TimeToStruct(monday, tm);
   tm.hour = MathMax(0, MathMin(23, InpSessionStartHour));
   tm.min = 0;
   tm.sec = 0;

   datetime anchor = StructToTime(tm);
   if(bar_time < anchor)
      anchor -= 7 * 86400;

   return anchor;
}

datetime AnchorStart(datetime bar_time)
{
   if(InpVwapAnchorMode == 1)
      return WeeklyAnchorStart(bar_time);

   return DailyAnchorStart(bar_time);
}

bool VwapAtShift(int shift, double &vwap)
{
   if(shift < 0 || Bars(_Symbol, _Period) <= shift)
      return false;

   datetime anchor = AnchorStart(iTime(_Symbol, _Period, shift));
   double price_volume_sum = 0.0;
   double volume_sum = 0.0;

   int max_shift = MathMin(Bars(_Symbol, _Period) - 1, shift + InpMaxVwapBars - 1);
   for(int i = shift; i <= max_shift; i++)
   {
      datetime bar_time = iTime(_Symbol, _Period, i);
      if(bar_time < anchor)
         break;

      double typical = (iHigh(_Symbol, _Period, i) + iLow(_Symbol, _Period, i) + iClose(_Symbol, _Period, i)) / 3.0;
      double volume = (double)iVolume(_Symbol, _Period, i);
      if(volume <= 0.0)
         volume = 1.0;

      price_volume_sum += typical * volume;
      volume_sum += volume;
   }

   if(volume_sum <= 0.0)
      return false;

   vwap = price_volume_sum / volume_sum;
   return true;
}

bool DeviationAtShift(int shift, double &deviation)
{
   double vwap = 0.0;
   if(!VwapAtShift(shift, vwap))
      return false;

   deviation = iClose(_Symbol, _Period, shift) - vwap;
   return true;
}

bool DeviationZAtShift(int shift, double &z_score, double &vwap)
{
   if(!VwapAtShift(shift, vwap))
      return false;

   double deviations[];
   int count = InpDeviationStdLookback;
   ArrayResize(deviations, count);

   double sum = 0.0;
   for(int i = 0; i < count; i++)
   {
      double deviation = 0.0;
      if(!DeviationAtShift(shift + i, deviation))
         return false;

      deviations[i] = deviation;
      sum += deviation;
   }

   double mean = sum / count;
   double variance = 0.0;
   for(int i = 0; i < count; i++)
      variance += MathPow(deviations[i] - mean, 2.0);

   double std_dev = MathSqrt(variance / count);
   if(std_dev <= 0.0)
      return false;

   z_score = (iClose(_Symbol, _Period, shift) - vwap) / std_dev;
   return true;
}

int VwapCrossCount(int signal_shift)
{
   int crosses = 0;

   for(int i = signal_shift; i < signal_shift + InpVwapCrossLookback - 1; i++)
   {
      double current_deviation = 0.0;
      double previous_deviation = 0.0;
      if(!DeviationAtShift(i, current_deviation) || !DeviationAtShift(i + 1, previous_deviation))
         continue;

      if((current_deviation > 0.0 && previous_deviation < 0.0) ||
         (current_deviation < 0.0 && previous_deviation > 0.0))
      {
         crosses++;
      }
   }

   return crosses;
}

double HighestClose(int first_shift, int count)
{
   double highest = iClose(_Symbol, _Period, first_shift);

   for(int i = first_shift + 1; i < first_shift + count; i++)
      highest = MathMax(highest, iClose(_Symbol, _Period, i));

   return highest;
}

double LowestClose(int first_shift, int count)
{
   double lowest = iClose(_Symbol, _Period, first_shift);

   for(int i = first_shift + 1; i < first_shift + count; i++)
      lowest = MathMin(lowest, iClose(_Symbol, _Period, i));

   return lowest;
}

bool RegimeFilterPass(int signal_shift, double atr)
{
   double current_vwap = 0.0;
   double old_vwap = 0.0;

   if(!VwapAtShift(signal_shift, current_vwap) ||
      !VwapAtShift(signal_shift + InpVwapSlopeBars, old_vwap))
      return false;

   if(atr <= 0.0)
      return false;

   if(InpMinAtrPoints > 0.0 && atr < InpMinAtrPoints)
      return false;

   if(MathAbs(current_vwap - old_vwap) > InpMaxVwapSlopeAtr * atr)
      return false;

   if(VwapCrossCount(signal_shift) < InpMinVwapCrosses)
      return false;

   double adx = 0.0;
   if(!CopyIndicatorValue(adx_handle, 0, signal_shift, adx))
      return false;

   return adx < InpMaxAdx;
}

void ResetActiveState()
{
   active_position_ticket = 0;
   active_entry_bar_time = 0;
}

void ManageOpenPosition()
{
   ulong ticket = FindStrategyPositionTicket();
   if(ticket == 0)
   {
      ResetActiveState();
      return;
   }

   if(!PositionSelectByTicket(ticket))
      return;

   long type = PositionGetInteger(POSITION_TYPE);
   int direction = type == POSITION_TYPE_BUY ? 1 : -1;
   datetime position_time = (datetime)PositionGetInteger(POSITION_TIME);
   double entry_price = PositionGetDouble(POSITION_PRICE_OPEN);

   if(active_position_ticket != ticket)
   {
      active_position_ticket = ticket;
      active_entry_bar_time = position_time;
   }

   int signal_shift = 1;
   double atr = 0.0;
   double z_score = 0.0;
   double vwap = 0.0;

   if(!CopyIndicatorValue(atr_handle, 0, signal_shift, atr))
      return;

   if(!DeviationZAtShift(signal_shift, z_score, vwap))
      return;

   double last_high = iHigh(_Symbol, _Period, signal_shift);
   double last_low = iLow(_Symbol, _Period, signal_shift);
   double last_close = iClose(_Symbol, _Period, signal_shift);

   if((direction > 0 && last_high >= vwap) || (direction < 0 && last_low <= vwap))
   {
      ClosePositionWithReason(ticket, "vwap_touch");
      return;
   }

   if(MathAbs(z_score) <= InpExitZ)
   {
      ClosePositionWithReason(ticket, "z_reversion");
      return;
   }

   datetime entry_time = active_entry_bar_time > 0 ? active_entry_bar_time : position_time;
   int entry_shift = iBarShift(_Symbol, _Period, entry_time, false);
   if(entry_shift >= InpMaxHoldBars)
   {
      ClosePositionWithReason(ticket, "timeout");
      return;
   }

   double adverse_distance = InpAdverseAtrMultiplier * atr;
   if(direction > 0 && last_close <= entry_price - adverse_distance)
   {
      ClosePositionWithReason(ticket, "adverse_atr_close");
      return;
   }

   if(direction < 0 && last_close >= entry_price + adverse_distance)
   {
      ClosePositionWithReason(ticket, "adverse_atr_close");
      return;
   }
}

bool BuildSignal(int &direction, double &entry, double &virtual_stop)
{
   if(Bars(_Symbol, _Period) < MaxLookbackNeeded())
      return false;

   int signal_shift = 1;
   double atr = 0.0;
   double z_score = 0.0;
   double vwap = 0.0;

   if(!CopyIndicatorValue(atr_handle, 0, signal_shift, atr))
      return false;

   if(!RegimeFilterPass(signal_shift, atr))
      return false;

   if(!DeviationZAtShift(signal_shift, z_score, vwap))
      return false;

   double signal_open = iOpen(_Symbol, _Period, signal_shift);
   double signal_close = iClose(_Symbol, _Period, signal_shift);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double adverse_distance = InpAdverseAtrMultiplier * atr;

   double previous_highest_close = HighestClose(signal_shift + 1, InpFreshCloseLookback);
   double previous_lowest_close = LowestClose(signal_shift + 1, InpFreshCloseLookback);

   bool short_setup = z_score > InpEntryZ
      && signal_close < signal_open
      && signal_close < previous_highest_close;

   bool long_setup = z_score < -InpEntryZ
      && signal_close > signal_open
      && signal_close > previous_lowest_close;

   if(long_setup)
   {
      direction = 1;
      entry = ask;
      virtual_stop = NormalizeDouble(entry - adverse_distance, _Digits);
      return entry > virtual_stop;
   }

   if(short_setup)
   {
      direction = -1;
      entry = bid;
      virtual_stop = NormalizeDouble(entry + adverse_distance, _Digits);
      return entry < virtual_stop;
   }

   return false;
}

int OnInit()
{
   if(InpRequireH1OrH4 && _Period != PERIOD_H1 && _Period != PERIOD_H4)
      return INIT_PARAMETERS_INCORRECT;

   starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);

   atr_handle = iATR(_Symbol, _Period, InpAtrPeriod);
   if(atr_handle == INVALID_HANDLE)
      return INIT_FAILED;

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

   if(trade_log_handle != INVALID_HANDLE)
      FileClose(trade_log_handle);

   if(equity_log_handle != INVALID_HANDLE)
      FileClose(equity_log_handle);

   if(bar_log_handle != INVALID_HANDLE)
      FileClose(bar_log_handle);

   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);

   if(adx_handle != INVALID_HANDLE)
      IndicatorRelease(adx_handle);
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

   if(HasOpenPosition())
      return;

   int direction = 0;
   double entry = 0.0;
   double virtual_stop = 0.0;

   if(!BuildSignal(direction, entry, virtual_stop))
      return;

   double volume = PositionSizeForRisk(entry, virtual_stop);
   if(volume <= 0.0)
      return;

   bool opened = false;
   if(direction > 0 && InpAllowLong)
      opened = trade.Buy(volume, _Symbol, entry, 0.0, 0.0, InpStrategyId);
   else if(direction < 0 && InpAllowShort)
      opened = trade.Sell(volume, _Symbol, entry, 0.0, 0.0, InpStrategyId);

   if(opened)
   {
      active_position_ticket = FindStrategyPositionTicket();
      active_entry_bar_time = current_bar;
   }
}
