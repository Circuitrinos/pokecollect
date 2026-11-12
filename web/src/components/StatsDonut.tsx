"use client";
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts";

const COLORS = [
  "#EF5350", "#FFCA28", "#42A5F5", "#66BB6A", "#AB47BC",
  "#26C6DA", "#FF7043", "#8D6E63", "#78909C", "#EC407A",
];

type Props = { data: Record<string, number>; title: string };

// Ring in its own box, legend as a list below it: however many rows the legend
// wraps into, it never covers or squeezes the ring (the recharts legend shared
// the chart's fixed height and drew over it).
export default function StatsDonut({ data, title }: Props) {
  const entries = Object.entries(data)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([name, value]) => ({ name, value }));

  return (
    <div>
      <h3 className="text-gray-300 font-medium mb-2">{title}</h3>
      <div className="h-[200px]" data-testid="stats-donut-ring">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={entries}
              innerRadius="62%"
              outerRadius="92%"
              dataKey="value"
              paddingAngle={2}
            >
              {entries.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              formatter={(v: number, name: string) => [v, name]}
              contentStyle={{ background: "#1f2937", border: "none", color: "#fff" }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-3 flex flex-wrap justify-center gap-x-3 gap-y-1" data-testid="stats-donut-legend">
        {entries.map((e, i) => (
          <li key={e.name} className="flex items-center gap-1.5 text-xs text-gray-300">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
            {e.name}
            <span className="tabular-nums text-gray-500">{e.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
