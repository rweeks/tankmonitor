var tankmonitor = {
    setup_graph: function ($chart_holder) {
        var units = $chart_holder.data('log-unit');
        d3.json($chart_holder.data('json-src'), function (data) {
            nv.addGraph(function () {

                var chart = nv.models.lineChart()
                        .margin({left: 70})
                        .useInteractiveGuideline(true)
                        .transitionDuration(350)
                        .showLegend(true)
                        .showXAxis(true)
                        .showYAxis(true)
                        .x(function (d) {
                            return d[0]
                        })
                        .y(function (d) {
                            return d[1]
                        })
                        .color(d3.scale.category10().range())
                    ;
                var precision = units === "density" ? '0.04f' : '0.02f';
                chart.yAxis
                    .axisLabel(units)
                    .tickFormat(d3.format(precision));
                chart.xAxis
                    .axisLabel("Time")
                    .tickFormat(function (d) {
                        return d3.time.format($chart_holder.data('time-fmt'))(new Date(d * 1000))
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

    render_valve_state: function (data) {
        var v_state = data['valve'],
            v_transition = data['transition_time'],
            state_msg = "Unknown valve state.",
            btn_msg = "Toggle Valve";
        v_transition = (v_transition == null) ? "" : " since " + v_transition;
        state_msg = v_state ? "Valve is closed (GPIO High)" + v_transition :
        "Valve is open (GPIO low)" + v_transition;
        btn_msg = v_state ? "Open Valve" : "Close Valve";
        $('.valve-state-desc').text(state_msg);
        $('button.valve-state-btn').text(btn_msg).removeClass('disabled');
    },

    clear_valve_control_error: function() {
        $('p.valve-control-error').text('');
    },

    show_valve_control_error: function(jqXHR, textStatus, errorThrown) {
        $('p.valve-control-error').text(errorThrown);
    },

    clear_current_value: function() {
        $('#current-value').html('loading...');
        $('#current-log-unit').html('');
    },

    select_category: function(category) {
        console.log("In select_category, category == " + category);
        tankmonitor.clear_current_value();
        if (category == null) {
            category = $('.category-select:visible').val();
        }
        console.log("Using category == " + category);
        $('.metric-category').hide();
        $('.metric-category[data-category="' + category + '"]').show();
    },

    get_selected_category: function() {
        return $('.metric-category:visible').data('category')
    },

    category_precision: {
        'density': 4,
        'water_temp': 1,
        'depth': 0
    },

    on_load: function () {
        $('.category-select').on('change', function() { tankmonitor.select_category(); });
        tankmonitor.select_category('depth')
        var event_sock = new SockJS('/event');
        event_sock.onmessage = function (e) {
            var $current_depth = $('#current-value');
            var $current_unit = $('#current-log-unit')
            e = $.parseJSON(e.data);
            if (e.event === 'log_value' && e.category === tankmonitor.get_selected_category()) {
                $current_depth.html(e.value.toFixed(tankmonitor.category_precision[e.unit]));
                var unit_label_html = e.category === 'density' ? 'g/cm<sup>3</sup>' : e.unit;
                $current_unit.html(unit_label_html)
            }
        };
        $('div.tankchart').each(function (ix, elem) {
            tankmonitor.setup_graph($(elem));
        });
        $('#valve-tab-link').on('shown.bs.tab', function () {
            tankmonitor.clear_valve_control_error();
            $.get('/valve', tankmonitor.render_valve_state);
        });
        $('button.valve-state-btn').on('click', function () {
            tankmonitor.clear_valve_control_error();
            $.ajax({
                type:'POST',
                url:'/valve',
                success: tankmonitor.render_valve_state,
                error: tankmonitor.show_valve_control_error
            });
        });
    }
};
