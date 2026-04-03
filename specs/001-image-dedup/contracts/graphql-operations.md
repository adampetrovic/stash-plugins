# GraphQL Operations Contract

All Stash interactions use the GraphQL API at `{scheme}://localhost:{port}/graphql`.

## Queries

### FindUnprocessedImages
Find images NOT tagged with `auto:dedup` (need fingerprinting).
```graphql
query FindImages($filter: FindFilterType, $image_filter: ImageFilterType) {
    findImages(filter: $filter, image_filter: $image_filter) {
        count
        images {
            id title details rating100
            visual_files { ... on ImageFile { path width height size } }
            studio { id name }
            tags { id name }
            performers { id name }
            galleries { id title }
        }
    }
}
# Variables: filter by tag EXCLUDES auto:dedup, paginated
```

### FindImagesByTag
Find images in a specific duplicate group.
```graphql
# Same query as above, filter by tag INCLUDES dedup:group:NNNN
```

### FindImage
Get a single image by ID (for hook processing).
```graphql
query FindImage($id: ID!) {
    findImage(id: $id) {
        id title details rating100
        visual_files { ... on ImageFile { path width height size } }
        studio { id name }
        tags { id name }
        performers { id name }
        galleries { id title }
    }
}
```

### FindTags
Look up a tag by exact name.
```graphql
query FindTags($filter: FindFilterType, $tag_filter: TagFilterType) {
    findTags(filter: $filter, tag_filter: $tag_filter) {
        tags { id name }
    }
}
```

## Mutations

### TagCreate
Create `auto:dedup` or `dedup:group:NNNN` tags.
```graphql
mutation TagCreate($input: TagCreateInput!) {
    tagCreate(input: $input) { id }
}
```

### ImageUpdate
Add/update tags, performers, rating on an image (used for processing tag and group tag assignment, and metadata merge during resolution).
```graphql
mutation ImageUpdate($input: ImageUpdateInput!) {
    imageUpdate(input: $input) { id }
}
# Input: { id, tag_ids, performer_ids, rating100, gallery_ids }
```

### ImageDestroy
Delete a duplicate image during resolution.
```graphql
mutation ImageDestroy($input: ImageDestroyInput!) {
    imageDestroy(input: $input)
}
# Input: { id, delete_file: true, delete_generated: true }
```

### AddImagesToGallery
Add keeper image to galleries from deleted duplicates.
```graphql
mutation AddImages($input: GalleryAddInput!) {
    addImagesToGallery(input: $input)
}
# Input: { gallery_id, image_ids: [keeper_id] }
```

### TagDestroy
Clean up orphaned group tags.
```graphql
mutation TagDestroy($input: TagDestroyInput!) {
    tagDestroy(input: $input)
}
# Input: { id }
```
