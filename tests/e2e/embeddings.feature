@API
Feature: embeddings

  Embeds questions and passages with a multilingual model, and ranks passages by how well they answer a question.

  Scenario: similarity-ranks-the-answer-first
    Given a Danish question
    And two English candidates, only one of which answers it
    When I send them to POST /embeddings/similarity
    Then I receive 200
    And every candidate is scored
    And the answering candidate is ranked first

  Scenario: no-candidates-is-rejected
    Given a question
    And an empty list of candidates
    When I send them to POST /embeddings/similarity
    Then I receive 422
    And the body is an application/problem+json document
    And its type is validation-error
    And its errors name body.candidates

  Scenario: question-becomes-a-query-vector
    Given a Danish question
    When I send it to POST /embeddings/query
    Then I receive 200
    And the body names the model and 1024 dimensions
    And the embedding has as many values as the dimensions
    And the embedding is unit-normalised
    And it answers within 5 seconds, with the model already loaded

  Scenario: documents-become-passage-vectors-in-order
    Given two short English documents
    When I send them to POST /embeddings/passages
    Then I receive 200
    And each document comes back as one chunk, in the order sent
    And every chunk carries its document's text
    And every chunk's embedding has 1024 values and is unit-normalised
