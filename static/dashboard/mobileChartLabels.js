(function () {
  function formatChartValue(value) {
    if (value && typeof value === "object") {
      if (value.y !== undefined && value.y !== null) {
        value = value.y;
      } else if (value.r !== undefined && value.r !== null) {
        value = value.r;
      } else {
        value = value.x;
      }
    }

    if (value === null || value === undefined || value === "") {
      return "";
    }

    var numberValue = Number(value);
    if (!Number.isFinite(numberValue) || numberValue === 0) {
      return "";
    }

    return Number.isInteger(numberValue)
      ? numberValue.toString()
      : numberValue.toFixed(1);
  }

  function getElementCenter(element, chart) {
    if (!element) {
      return null;
    }

    if (typeof element.getProps === "function") {
      var props = element.getProps(
        ["x", "y", "base", "width", "height"],
        true
      );
      if (
        Number.isFinite(props.x) &&
        Number.isFinite(props.y) &&
        Number.isFinite(props.base)
      ) {
        if (chart.options && chart.options.indexAxis === "y") {
          return { x: (props.x + props.base) / 2, y: props.y };
        }
        return { x: props.x, y: (props.y + props.base) / 2 };
      }
    }

    if (typeof element.tooltipPosition === "function") {
      return element.tooltipPosition();
    }

    if (typeof element.getCenterPoint === "function") {
      return element.getCenterPoint();
    }

    if (element.x !== undefined && element.y !== undefined) {
      return { x: element.x, y: element.y };
    }

    if (element._model) {
      return { x: element._model.x, y: element._model.y };
    }

    return null;
  }

  function drawMobileChartLabels(chart) {
    var ctx = chart.ctx;
    if (!ctx || !chart.data || !Array.isArray(chart.data.datasets)) {
      return;
    }

    ctx.save();
    ctx.font = "600 11px Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
    ctx.fillStyle = "#1f2937";

    chart.data.datasets.forEach(function (dataset, datasetIndex) {
      var meta = chart.getDatasetMeta(datasetIndex);
      if (!meta || meta.hidden || !Array.isArray(meta.data)) {
        return;
      }

      meta.data.forEach(function (element, dataIndex) {
        if (element && element.hidden) {
          return;
        }

        var value = dataset.data && dataset.data[dataIndex];
        var label = formatChartValue(value);
        var center = getElementCenter(element, chart);

        if (!label || !center) {
          return;
        }

        ctx.strokeText(label, center.x, center.y);
        ctx.fillText(label, center.x, center.y);
      });
    });

    ctx.restore();
  }

  function registerMobileChartLabels() {
    if (!window.Chart || window.Chart._mobileDashboardValueLabelsRegistered) {
      return;
    }

    var plugin = {
      id: "mobileDashboardValueLabels",
      afterDatasetsDraw: drawMobileChartLabels,
    };

    if (typeof window.Chart.register === "function") {
      window.Chart.register(plugin);
    } else if (window.Chart.plugins && window.Chart.plugins.register) {
      window.Chart.plugins.register(plugin);
    }

    window.Chart._mobileDashboardValueLabelsRegistered = true;
  }

  registerMobileChartLabels();
})();
