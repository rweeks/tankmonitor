var tankmonitor = {
    setup_graph: function($chart_holder) {
        var units = $chart_holder.data('log-unit');
        d3.json($chart_holder.data('json-src'), function(data) {
          nv.addGraph(function() {

              var chart = nv.models.lineChart()
                      .margin({left: 70})
                      .useInteractiveGuideline(true)
                      .transitionDuration(350)
                      .showLegend(true)
                      .showXAxis(true)
                      .showYAxis(true)
                      .x(function(d) { return d[0] })
                      .y(function(d) { return d[1] })
                      .color(d3.scale.category10().range())
                  ;

            chart.yAxis
                .axisLabel(units)
                .tickFormat(d3.format('.02f'));
            chart.xAxis
                .axisLabel("Time")
                .tickFormat(function(d) {
                    return d3.time.format($chart_holder.data('time-fmt'))(new Date(d*1000))
                });
            d3.select($chart_holder.find('svg')[0])
                .datum([data])
                .call(chart);

            //TODO: Figure out a good way to do this automatically
            nv.utils.windowResize(chart.update);

            return chart;
          });
        });
    },

    activate_valve_tab: function() {
        console.log("Activating valve tab");
    },

    on_load: function() {
        var event_sock = new SockJS('/event');
        event_sock.onmessage = function(e) {
            var $current_depth=$('#current-value');
            e = $.parseJSON(e.data);
            if (e.event == 'log_value') {
                $current_depth.html(e.value.toFixed());
            }
        };
        $('div.tankchart').each(function(ix, elem) {
            tankmonitor.setup_graph($(elem));
        });
        $('#valve-tab-link').on('shown.bs.tab', tankmonitor.activate_valve_tab);
    }
};
