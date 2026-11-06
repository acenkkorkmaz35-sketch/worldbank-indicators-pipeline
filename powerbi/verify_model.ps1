# Verifies the Power BI model in a running Power BI Desktop, without touching the GUI.
#
#   1. Open powerbi\WorldBankIndicators.pbip in Power BI Desktop (Windows).
#   2. powershell -ExecutionPolicy Bypass -File powerbi\verify_model.ps1   [-NoRefresh]
#
# It connects to Desktop's local Analysis Services engine (as DAX Studio / Tabular Editor do),
# refreshes the model (unless -NoRefresh), reports load and measure errors, then runs the DAX
# measures and compares every value with the SQL reporting layer in output\*.csv.
param([string]$Repo = (Split-Path $PSScriptRoot -Parent), [switch]$NoRefresh)
$ErrorActionPreference = "Stop"
$bin = "C:\Program Files\Microsoft Power BI Desktop\bin"
[void][Reflection.Assembly]::LoadFrom("$bin\Microsoft.PowerBI.Tabular.dll")
[void][Reflection.Assembly]::LoadFrom("$bin\Microsoft.PowerBI.AdomdClient.dll")
$inv = [Globalization.CultureInfo]::InvariantCulture

# --- find the engine of the running Desktop (newest workspace whose port answers) ---
$root = "$env:LOCALAPPDATA\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces"
$ports = Get-ChildItem $root -Recurse -Filter msmdsrv.port.txt -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
$server = $null
foreach ($pf in $ports) {
    $port = [Text.Encoding]::Unicode.GetString([IO.File]::ReadAllBytes($pf.FullName)).Trim()
    try { $s = New-Object Microsoft.AnalysisServices.Tabular.Server; $s.Connect("Data Source=localhost:$port"); $server = $s; break } catch { }
}
if (-not $server) { throw "no running Power BI Desktop engine found" }
$db = $server.Databases[0]
$model = $db.Model
"engine: localhost:$port  database: $($db.Name)  compatibility: $($db.CompatibilityLevel)"
"DataFolder = " + ($model.Expressions | Where-Object Name -eq "DataFolder").Expression.Split('"')[1]

# --- structure ---
"tables: " + (($model.Tables | ForEach-Object { "$($_.Name)($($_.Columns.Count) cols, $($_.Measures.Count) measures)" }) -join ", ")
"relationships: " + (($model.Relationships | ForEach-Object { "$($_.FromTable.Name).$($_.FromColumn.Name) -> $($_.ToTable.Name).$($_.ToColumn.Name)" }) -join "; ")
foreach ($t in $model.Tables) { foreach ($m in $t.Measures) { if ($m.ErrorMessage) { "MEASURE ERROR [$($m.Name)]: $($m.ErrorMessage)" } } }

# --- refresh ---
if (-not $NoRefresh) {
    try {
        $model.RequestRefresh([Microsoft.AnalysisServices.Tabular.RefreshType]::Full)
        $res = $model.SaveChanges()
        "refresh: ok"
    } catch {
        "REFRESH FAILED: $($_.Exception.Message)"
        $server.Refresh($true)
    }
}
$model = $server.Databases[0].Model
foreach ($t in $model.Tables) {
    foreach ($p in $t.Partitions) { if ($p.State -ne "Ready") { "PARTITION $($t.Name): state=$($p.State) $($p.ErrorMessage)" } }
}

# --- DAX ---
$conn = New-Object Microsoft.AnalysisServices.AdomdClient.AdomdConnection("Data Source=localhost:$port")
$conn.Open()
function Invoke-Dax([string]$q) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $q
    $r = $cmd.ExecuteReader(); $rows = @()
    while ($r.Read()) {
        $o = [ordered]@{}
        for ($i = 0; $i -lt $r.FieldCount; $i++) { $o[($r.GetName($i) -replace '^.*\[|\]$', '')] = $r.GetValue($i) }
        $rows += [pscustomobject]$o
    }
    $r.Close(); return $rows
}
function Num($x) { if ($x -is [DBNull] -or $null -eq $x -or "$x" -eq "") { return $null }; return [double]::Parse("$x", $inv) }
function Same($a, $b) { if ($null -eq $a -and $null -eq $b) { return $true }; if ($null -eq $a -or $null -eq $b) { return $false }; return [math]::Abs([double]$a - [double]$b) -le 1e-6 * [math]::Max([double]1.0, [math]::Abs([double]$b)) }

$counts = Invoke-Dax 'EVALUATE ROW("dim_country", COUNTROWS(dim_country), "dim_indicator", COUNTROWS(dim_indicator), "dim_year", COUNTROWS(dim_year), "fct", COUNTROWS(fct_indicator_value), "completeness", [Completeness %], "missing", [Missing Combinations], "countries", [Countries Reporting], "latest", [Latest Year With Data])'
"row counts / coverage: " + ($counts[0].PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", "

# Q4: income groups by year (+ ALL) vs rpt_income_group_by_year.csv
$expected = Import-Csv "$Repo\output\rpt_income_group_by_year.csv"
$groups = Invoke-Dax 'EVALUATE SUMMARIZECOLUMNS(dim_indicator[indicator_code], dim_country[income_group_code], dim_year[year], "v", [Indicator Value], "avg", [Simple Average])'
$all = Invoke-Dax 'EVALUATE SUMMARIZECOLUMNS(dim_indicator[indicator_code], dim_year[year], "v", [Indicator Value], "avg", [Simple Average])'
$got = @{}
foreach ($g in $groups) { $got["$($g.indicator_code)|$($g.income_group_code)|$($g.year)"] = $g }
foreach ($g in $all) { $got["$($g.indicator_code)|ALL|$($g.year)"] = $g }
$bad = 0
foreach ($e in $expected) {
    $k = "$($e.indicator_code)|$($e.income_group_code)|$($e.year)"
    $g = $got[$k]
    if (-not $g -or -not (Same (Num $g.v) (Num $e.group_value)) -or -not (Same (Num $g.avg) (Num $e.simple_avg_of_countries))) {
        $bad++; if ($bad -le 5) { "  MISMATCH $k : dax=$($g.v)/$($g.avg) sql=$($e.group_value)/$($e.simple_avg_of_countries)" }
    }
}
"Indicator Value + Simple Average vs rpt_income_group_by_year: $($expected.Count - $bad)/$($expected.Count) match"

# Q2: YoY % per country vs rpt_yoy_change.csv
$yexp = Import-Csv "$Repo\output\rpt_yoy_change.csv"
$yoy = Invoke-Dax 'EVALUATE SUMMARIZECOLUMNS(dim_indicator[indicator_code], dim_country[country_iso3], dim_year[year], "yoy", [YoY %], "v", [Indicator Value])'
$ygot = @{}; foreach ($y in $yoy) { $ygot["$($y.indicator_code)|$($y.country_iso3)|$($y.year)"] = $y }
$ybad = 0
foreach ($e in $yexp) {
    $k = "$($e.indicator_code)|$($e.country_iso3)|$($e.year)"
    $y = $ygot[$k]
    $dv = if ($y) { Num $y.yoy } else { $null }; $vv = if ($y) { Num $y.v } else { $null }
    if (-not (Same $dv (Num $e.pct_change)) -or -not (Same $vv (Num $e.value))) {
        $ybad++; if ($ybad -le 5) { "  MISMATCH $k : dax yoy=$dv value=$vv sql yoy=$($e.pct_change) value=$($e.value)" }
    }
}
"YoY % + country values vs rpt_yoy_change: $($yexp.Count - $ybad)/$($yexp.Count) match"

# guard: values of different indicators must never be added up
$mixed = Invoke-Dax 'EVALUATE ROW("all_indicators", [Indicator Value], "all_avg", [Simple Average])'
"Indicator Value with several indicators in context (expect blank): '$($mixed[0].all_indicators)' / '$($mixed[0].all_avg)'"
$conn.Close(); $server.Disconnect()
