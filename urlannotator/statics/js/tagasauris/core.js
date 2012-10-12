(function(){

    window.getTemplate = function(name){
        var rawTemplate = $("#" + name).html();
        rawTemplate = rawTemplate.replace(/&lt;/g, '<');
        rawTemplate = rawTemplate.replace(/&gt;/g, '>');
        return _.template(rawTemplate);
    };

    window.Sample = Backbone.Model.extend({

        added: null,

        matched: null,

        clear: function () {
            Samples.remove(this);
            $(this.view.el).remove();
        },

        update: function () {
            this.view.render();
        }

    });

    window.SampleList = Backbone.Collection.extend({

        model: Sample,

        getAdded: function () {
            return this.map(function (sample) {
                if (sample.added) {
                    return sample.attributes.url;
                }
            });
        }

    });

    window.BaseSampleView = Backbone.View.extend({

        tagName: "li",

        className: "sample",

        template: null,

        events: {
            "click .sample-remove": "clear"
        },

        initialize: function() {
            this.model.view = this;
        },

        render: function() {
            $(this.el).html(this.template(this.model));
            return this;
        },

        clear: function() {
            this.model.clear();
        }

    });

    window.BaseExternalApp = Backbone.View.extend({

        minSamples: 5,

        workerId: null, // It is set on Tagasauris side

        gathered: 0,

        el: $("#form-template"),

        template: null,

        samples: new SampleList(),

        templateValidatedSample: _.template(
            '<div class="open-question"><input type="text" name="new-sample"'+
            ' value="<%= url %>"></div>'),

        events: {
            "click .add-new-sample": "addNewSample",
            "keypress .new-sample": "checkAddNewSample"
        },

        initialize: function (data) {
            _.bindAll(this);
            this.samples.bind("add", this.renderPartial, this);
            this.samples.bind("remove", this.renderPartial, this);

            data = $.parseJSON(data);
            this.token = data.token;
            this.jobId = data.job_id;
            this.coreUrl = data.core_url;
            if (data.minSamples !== undefined) {
                this.minSamples = data.min_samples;
            }

            this.render();
        },

        render: function () {
            this.el.html(this.template());
            this.renderPartial();
        },

        renderPartial: function () {
            if (this.gathered >= this.minSamples) {
                this.$('.add-new-sample').attr('disabled', 'disabled');
            } else {
                this.$('.add-new-sample').removeAttr("disabled");
            }

            this.$('.sample-counter').html("Gathered: " +
                this.gathered.toString() + "/" + this.minSamples.toString());
        },

        checkAddNewSample: function (e) {
            if (e.keyCode == 13) {
                e.preventDefault();
                this.addNewSample();
            }
        },

        addNewSample: function () {
            // Not implemented
        },

        finishHIT: function () {
            this.clearValidatedSamples();
            this.prepareValidatedSamples();
            this.sendValidatedSamples();
        },

        clearValidatedSamples: function () {
            this.$(".validated-urls").html("");
        },

        prepareValidatedSamples: function () {
            var urls = this.samples.getAdded();
            for (var i = urls.length - 1; i >= 0; i--) {
                if (urls[i] !== undefined) {
                    this.$(".validated-urls").append(
                        this.templateValidatedSample({url: urls[i]})
                    );
                }
            }
        },

        sendValidatedSamples: function () {
            this.$(".form-vertical").submit();
        }

    });

}());