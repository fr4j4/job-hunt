// ECharts por módulos: solo lo que usamos (el resto no entra al bundle).
import * as echarts from 'echarts/core';
import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts';
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent,
         TooltipComponent, VisualMapComponent, BrushComponent, ToolboxComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';

echarts.use([BarChart, LineChart, ScatterChart, HeatmapChart, CustomChart, AriaComponent, DataZoomComponent,
             GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, VisualMapComponent,
             BrushComponent, ToolboxComponent, SVGRenderer]);
export { echarts };
// Las opciones se construyen como objetos sueltos (más cómodo que los tipos estrictos de ECharts).
export type EChartsOption = Record<string, any>;
