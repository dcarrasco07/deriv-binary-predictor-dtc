//+------------------------------------------------------------------+
//|                                                ProductionEA.mq5 |
//|                                  Copyright 2026, Dawn Carrasco |
//|                                           https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Dawn Carrasco"
#property link      "https://www.mql5.com"
#property version   "1.03"
#property description "Bulletproof Tick Exporter using OnTester"

//--- Input parameters
input string         InpFileName="ProductionEA_Ticks.csv"; // CSV file name

//--- Structure to store ticks in RAM safely
struct TickData
  {
   datetime time;
   double   bid;
   double   ask;
   double   last;
   long     volume;
  };

//--- Global Variables
TickData g_tick_buffer[];
int      g_ticks_stored = 0;
MqlTick  g_last_tick;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_ticks_stored = 0;
   ArrayResize(g_tick_buffer, 50000, 100000); // Pre-allocate memory array
   
   Print("EA Initialized. Buffering ticks in RAM memory...");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(SymbolInfoTick(Symbol(), g_last_tick))
     {
      // Resize dynamic array if it's getting full
      if(g_ticks_stored >= ArraySize(g_tick_buffer))
        {
         ArrayResize(g_tick_buffer, g_ticks_stored + 50000, 100000);
        }
        
      // Stream tick data directly into computer memory instead of hitting the disk 
      g_tick_buffer[g_ticks_stored].time   = g_last_tick.time;
      g_tick_buffer[g_ticks_stored].bid    = g_last_tick.bid;
      g_tick_buffer[g_ticks_stored].ask    = g_last_tick.ask;
      g_tick_buffer[g_ticks_stored].last   = g_last_tick.last;
      g_tick_buffer[g_ticks_stored].volume = g_last_tick.volume;
      
      g_ticks_stored++;
     }
  }

//+------------------------------------------------------------------+
//| Tester function                                                  |
//| This handler is natively called by MT5 precisely when the        |
//| backtest finishes, preventing sandbox wipeouts.                  |
//+------------------------------------------------------------------+
double OnTester()
  {
   Print("OnTester triggered. Attempting file dump of ", g_ticks_stored, " ticks...");

   if(g_ticks_stored == 0)
     {
      Print("No ticks were gathered to write.");
      return(0.0);
     }

   // Open file using FILE_COMMON to force it into the safe common folder
   int file_handle = FileOpen(InpFileName, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   
   // Backup: If Common folder is locked out by Windows, try local sandbox fallback
   if(file_handle == INVALID_HANDLE)
     {
      file_handle = FileOpen(InpFileName, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
     }
     
   if(file_handle != INVALID_HANDLE)
     {
      // Write Column Headers
      FileWrite(file_handle, "Time", "Bid", "Ask", "Last", "Volume");
      
      // Dump the buffered arrays cleanly out into the text file
      for(int i=0; i<g_ticks_stored; i++)
        {
         FileWrite(file_handle,
                   TimeToString(g_tick_buffer[i].time, TIME_DATE|TIME_SECONDS), 
                   DoubleToString(g_tick_buffer[i].bid, _Digits),
                   DoubleToString(g_tick_buffer[i].ask, _Digits),
                   DoubleToString(g_tick_buffer[i].last, _Digits),
                   (string)g_tick_buffer[i].volume);
        }
        
      FileFlush(file_handle);
      FileClose(file_handle);
      Print("SUCCESS: File successfully created with ", g_ticks_stored, " entries.");
     }
   else
     {
      Print("CRITICAL: File creation failed completely. Error code: ", GetLastError());
     }
     
   // Free system RAM resources
   ArrayFree(g_tick_buffer);

   return(0.0); // Return value required by MT5 for custom optimization criteria
  }
//+------------------------------------------------------------------+