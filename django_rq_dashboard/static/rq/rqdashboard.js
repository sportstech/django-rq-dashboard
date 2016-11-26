(function($) {
	var POLL_INTERVAL = 2500,
      qtemplate, noq_row, wtemplate, now_row, stemplate, nos_row, error_row,
	    error = function(xhr, textStatus, errorThrown) {
        var qtbody = $('#queues tbody');
        qtbody.empty();
        qtbody.append(error_row);

        var wtbody = $('#workers tbody');
        wtbody.empty();
        wtbody.append(error_row);

        var stbody = $('#scheduled tbody');
        stbody.empty();
        stbody.append(error_row);
      },
      success = function(data, textStatus, xhr) {
        if (textStatus != 'success') {
          return;
        }

        var qtbody = $('#queues tbody');
        qtbody.empty();

        if (data.queues.length > 0) {
          $.each(data.queues, function(i, queue) {
            queue.klass = i % 2 === 0 ? 'row2' : 'row1';
            qtbody.append(qtemplate(queue));
          });
        } else {
          qtbody.append(noq_row);
        }

        var wtbody = $('#workers tbody');
        wtbody.empty();

        if (data.workers.length > 0) {
          $.each(data.workers, function(i, worker) {
            worker.klass = i % 2 === 0 ? 'row2' : 'row1';
            worker.queues = worker.queues.join(', ');
            wtbody.append(wtemplate(worker));
          });
        } else {
          wtbody.append(now_row);
        }

        var stbody = $('#scheduled tbody');
        stbody.empty();

        if (data.scheduled_queues.length > 0) {
          $.each(data.scheduled_queues, function(i, queue) {
            queue.klass = i % 2 === 0 ? 'row2' : 'row1';
            stbody.append(stemplate(queue));
          });
        } else {
          stbody.append(nos_row);
        }
      },
      refresh = function() {
        $.ajax({
          url: window.location.href,
          dataType: 'json',
          success: success,
          error: error,
        });
      };

	$(document).ready(function() {
    qtemplate = _.template($('script[name=queue-row]').html());
    noq_row = $('script[name=no-queue-row]').html();
    wtemplate = _.template($('script[name=worker-row]').html());
    now_row = $('script[name=no-worker-row]').html();
    stemplate = _.template($('script[name=scheduled-row]').html());
    nos_row = $('script[name=no-scheduled-row]').html();
    error_row = $('script[name=error-row]').html();

    function poll() {
      refresh();
      setTimeout(poll, POLL_INTERVAL);
    }
    poll();
	});

})(django.jQuery);
